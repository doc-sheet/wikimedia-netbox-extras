"""A script to generate data used by hiera"""
from collections import defaultdict
from typing import Dict, List
from json import dumps as json_dump

from dcim.models import Device, Site
from extras.scripts import Script, StringVar


class HieraExport(Script):
    """Class to export Netbox data for hiera consumption"""

    class Meta:
        """Required by netbox"""
        name = "Hiera host definition export"
        description = "Return All hosts and there associated hiera information"
        filed_order = ['hostname', 'site_name']
        commit_default = False

        site_name = StringVar(description="The name of the site to filter by", required=False)
        hostname = StringVar(description="The hostname to filter by", required=False)

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
                'rack': device.rack.name,
                'rack_group': device.rack.group.slug,
                'status': device.status,
            }

        return hosts

    def run(self, data: Dict, commit: bool) -> str:
        """Required by netbox"""
        if 'hostname' in data:
            devices = [Device.objects.get(name=data['hostname'])]
        elif 'site_name' in data:
            site = Site.objects.get(slug=data['site_name'])
            devices = Device.objects.filter(site_id=site.id)
        else:
            devices = Device.objects.all()

        return json_dump(self._generate_hiera(devices))
