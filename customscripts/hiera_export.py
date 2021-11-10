"""A script to generate data used by hiera"""
import json

from collections import defaultdict
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
    def _generate_output(devices: List[Device]) -> Dict:
        """Generate the neccesary output dictionary

        Arguments:
            devices (List[Device]): a list of netbox devices

        Returns:
            dict: a dictionary of values for hiera

        """
        hosts = defaultdict(dict)

        for device in devices:
            hosts[device.name] = {
                'location': {
                    'site': device.site.slug,
                },
                'status': device.status,
            }
            if isinstance(device, Device):
                if device.rack:
                    hosts[device.name]['location']['rack'] = device.rack.name
                    if device.rack.group:
                        hosts[device.name]['location']['row'] = device.rack.group.slug
            if isinstance(device, VirtualMachine):
                if device.cluster:
                    hosts[device.name]['location']['cluster'] = device.cluster.name
        return {'hosts': hosts}

    # pylint: disable=unused-argument
    def run(self, data: Dict, commit: bool) -> str:
        """Required by netbox"""
        hw_devices = Device.objects.filter(
            device_role__slug="server", tenant__isnull=True
        ).exclude(status__in=HW_EXCLUDE_STATUSES)
        vm_devices = VirtualMachine.objects.filter(tenant__isnull=True).exclude(
            status__in=VM_EXCLUDE_STATUSES)
        all_hosts = self._generate_output(hw_devices)
        all_hosts.update(self._generate_output(vm_devices))
        return json.dumps(all_hosts)
