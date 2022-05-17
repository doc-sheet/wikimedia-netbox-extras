"""A script to generate data used by hiera"""
import json

from typing import Dict, List

from dcim.choices import DeviceStatusChoices
from dcim.models import Device
from virtualization.choices import VirtualMachineStatusChoices
from virtualization.models import VirtualMachine

from extras.scripts import Script

HW_EXCLUDE_STATUSES = (
    DeviceStatusChoices.STATUS_DECOMMISSIONING,
    DeviceStatusChoices.STATUS_INVENTORY,
    DeviceStatusChoices.STATUS_OFFLINE,
    DeviceStatusChoices.STATUS_PLANNED,
)
VM_EXCLUDE_STATUSES = (
    VirtualMachineStatusChoices.STATUS_DECOMMISSIONING,
    VirtualMachineStatusChoices.STATUS_OFFLINE,
    VirtualMachineStatusChoices.STATUS_PLANNED,
)


# pylint: disable=too-few-public-methods
class HieraExport(Script):
    """Class to export Netbox data for hiera consumption"""

    class Meta:
        """Required by netbox"""

        name = "Hiera host definition export"
        description = "Return All hosts and their associated hiera information"
        commit_default = False

    @staticmethod
    def _generate_network_devices(devices: List[Device]) -> Dict:
        """Generate the necessary output dictionary

        Arguments:
            devices (Dict[str, Device]): a dict of netbox devices

        Returns:
            dict: a dictionary of values for hiera

        """
        hosts = HieraExport._generate_hosts(devices)
        for device in devices:
            hosts[device.name]["role"] = device.device_role.slug
            hosts[device.name]["networking"] = {
                "ip": device.primary_ip4,
                "ip6": device.primary_ip6,
            }

        return hosts

    @staticmethod
    def _generate_hosts(devices: List[Device]) -> Dict:
        """Generate the necessary output dictionary

        Arguments:
            devices (List[Device]): a list of netbox devices

        Returns:
            dict: a dictionary of values for hiera

        """
        hosts = {}

        for device in devices:
            hosts[device.name] = {
                # profile::netbox::host will load this data into netbox::host
                # We can then make the data available via netbox::$functions
                "location": {
                    "site": device.site.slug,
                },
                "status": device.status,
            }
            # Covers bare metal servers and network devices
            if isinstance(device, Device):
                if device.rack:
                    hosts[device.name]["location"]["rack"] = device.rack.name
                    if device.rack.location:
                        hosts[device.name]["location"][
                            "row"
                        ] = device.rack.location.slug
            if isinstance(device, VirtualMachine):
                if device.cluster:
                    hosts[device.name]["location"]["cluster"] = device.cluster.name
        return hosts

    def run(self, data: Dict, commit: bool) -> str:
        """Required by netbox"""
        # pylint: disable=unused-argument
        results = {"hosts": {}}
        network_slugs = {
            "core_routers": "cr",
            "access_switches": "aws",
            "frack_firewalls": "pfw",
            "cloud_switchs": "cloudsw",
            "mgmt_routers": "mr",
            "mgmt_switches": "msw",
            "pdu": "pdu",
            "console_server": "scs",
        }
        unmanaged_devices = {}
        for category, slug in network_slugs.items():
            devices = Device.objects.filter(device_role__slug=slug).exclude(
                status__in=HW_EXCLUDE_STATUSES
            )
            unmanaged_devices[category] = self._generate_network_devices(devices)

        unmanaged_devices["atlas_anchors"] = self._generate_network_devices(
            Device.objects.filter(
                device_role__slug="server", tenant__sluf="ripe-ncc"
            ).exclude(status__in=HW_EXCLUDE_STATUSES)
        )

        hw_devices = Device.objects.filter(
            device_role__slug="server", tenant__isnull=True
        ).exclude(status__in=HW_EXCLUDE_STATUSES)

        vm_devices = VirtualMachine.objects.filter(tenant__isnull=True).exclude(
            status__in=VM_EXCLUDE_STATUSES
        )
        results["hosts"].update(self._generate_hosts(hw_devices))
        results["hosts"].update(self._generate_hosts(vm_devices))
        results["unmanaged_devices"] = unmanaged_devices
        return json.dumps(results)
