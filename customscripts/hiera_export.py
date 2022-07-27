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
                'location': {
                    'site': device.site.slug,
                },
                'status': device.status,
            }
            if isinstance(device, Device):
                if device.rack:
                    hosts[device.name]['location']['rack'] = device.rack.name
                    if device.rack.location:
                        hosts[device.name]['location']['row'] = device.rack.location.slug
            if isinstance(device, VirtualMachine):
                if device.cluster:
                    hosts[device.name]['location']['ganeti_group'] = device.cluster.name
                    hosts[device.name]['location']['ganeti_cluster'] = device.cluster.group.name
        return hosts

    @staticmethod
    def _generate_mgmt(devices: List[Device]) -> Dict:
        """Generate the list of management hostnames, keyed by site

        Arguments:
            devices (List[Device]): a list of netbox devices

        Returns:
            dict: a dictionary of values for hiera

        """
        mgmt = {}

        for device in devices:
            if 'mgmt' not in [x.name for x in device.interfaces.all()]:
                continue

            fqdn = f'{device.name}.mgmt.{device.site.slug}.wmnet'
            data = {
                'row': device.rack.location.slug,
                'rack': device.rack.name,
            }

            site_mgmt = mgmt.setdefault(device.site.slug, {})
            site_mgmt[fqdn] = data

        return mgmt

    def run(self, data: Dict, commit: bool) -> str:
        """Required by netbox"""
        # pylint: disable=unused-argument
        results = {'hosts': {}, 'mgmt': {}}
        hw_devices = Device.objects.filter(
            device_role__slug="server", tenant__isnull=True
        ).exclude(status__in=HW_EXCLUDE_STATUSES)
        vm_devices = VirtualMachine.objects.filter(tenant__isnull=True).exclude(
            status__in=VM_EXCLUDE_STATUSES)
        results['hosts'].update(self._generate_hosts(hw_devices))
        results['hosts'].update(self._generate_hosts(vm_devices))
        results['mgmt'].update(self._generate_mgmt(hw_devices))
        return json.dumps(results)
