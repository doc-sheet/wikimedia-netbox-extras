"""A script to generate data used by hiera"""
import json

from collections import defaultdict
from typing import Dict, List

from dcim.choices import DeviceStatusChoices
from dcim.models import Device
from extras.scripts import Script

EXCLUDE_STATUSES = (
    DeviceStatusChoices.STATUS_DECOMMISSIONING,
    DeviceStatusChoices.STATUS_INVENTORY,
    DeviceStatusChoices.STATUS_OFFLINE,
    DeviceStatusChoices.STATUS_PLANNED,
)


class HieraExport(Script):
    """Class to export Netbox data for hiera consumption"""

    hiera_prefix = 'profile::netbox'
    host_prefix = f'{hiera_prefix}::host'

    class Meta:
        """Required by netbox"""

        name = "Hiera host definition export"
        description = "Return All hosts and their associated hiera information"
        commit_default = False

    def _generate_hiera(self, devices: List[Device]) -> Dict:
        """Generate the neccesary output dictionary

        Arguments:
            devices (List[Device]): a list of netbox devices

        Returns:
            dict: a dictionary of values for hiera

        """
        hosts = defaultdict(dict)

        for device in devices:
            hosts[device.name] = {
                # profile::netbox::host will load this data into netbox::host
                # We can then make the data available via netbox::$functions
                '{self.host_prefix}::location': {
                    'site': device.site.slug,
                    'rack': device.rack.name if device.rack else None,
                    'row': device.rack.group.slug
                    if device.rack and device.rack.group
                    else None,
                },
                f'{self.host_prefix}::status': device.status,
            }

        return {'hosts': hosts}

    # pylint: disable=unused-argument
    def run(self, data: Dict, commit: bool) -> str:
        """Required by netbox"""
        devices = Device.objects.filter(
            device_role__slug="server", tenant__isnull=True
        ).exclude(status__in=EXCLUDE_STATUSES)

        return json.dumps(self._generate_hiera(devices))
