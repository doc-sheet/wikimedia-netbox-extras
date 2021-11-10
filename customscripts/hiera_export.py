"""A script to generate data used by hiera"""
import json

from collections import defaultdict
from typing import Dict, List

from dcim.models import Device
from extras.scripts import Script


class HieraExport(Script):
    """Class to export Netbox data for hiera consumption"""

    class Meta:
        """Required by netbox"""
        name = "Hiera host definition export"
        description = "Return All hosts and their associated hiera information"
        commit_default = False

    @staticmethod
    def _generate_hiera(devices: List[Device]) -> Dict:
        """Generate the neccesary output dictionary

        Arguments:
            devices (List[Device]): a list of netbox devices

        Returns:
            dict: a dictionary of values for hiera

        """
        hosts = defaultdict(dict)

        for device in devices:
            hosts[device.name] = {
                'site': device.site.slug,
                'rack': device.rack.name if device.rack else None,
                'rack_group': device.rack.group.slug if device.rack and device.rack.group else None,
                'status': device.status,
            }

        return hosts

    # pylint: disable=unused-argument
    def run(self, data: Dict, commit: bool) -> str:
        """Required by netbox"""
        devices = Device.objects.all()

        return json.dumps(self._generate_hiera(devices))
