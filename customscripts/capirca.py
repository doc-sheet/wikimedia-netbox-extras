"""Generates ready to consume capirca definiton files."""

import re

from collections import Counter
from io import StringIO

from dcim.models import Device
from virtualization.models import VirtualMachine

from extras.scripts import Script


class GetHosts(Script):
    class Meta:
        name = "Capirca hosts definitions"
        description = "Returns all the Netbox host IPs in a Capirca NETWORK.def format."

    def process_device_vm(self, host):
        # TODO Vritual chassis ?
        # TODO ignore decom hosts
        # Assume all hosts will have at least a v4 address
        if not host.primary_ip4:
            return

        # create the grouping key (device prefix) best effort
        search = re.search('^(.+)\d\d\d\d$', host.name)
        if search:
            group = search.group(1)
            if search.group(1) in self.groups:
                self.groups[group].add(host.name)
            else:
                self.groups[group] = set([host.name])

        # Keep decent indentation
        prefix = '{} = '.format(host.name)
        v4_address = str(host.primary_ip4.address.ip)
        self.output.write('{}{} # {}\n'.format(prefix, v4_address, host.name))
        if host.primary_ip6:
            v6_address = str(host.primary_ip6.address.ip)
            self.output.write('{} # {}\n'.format(v6_address.rjust(len(prefix)), host.name))

    def run(self, data, commit):
        self.groups = {}
        self.output = StringIO()
        
        # We get all the devices that have a primary IP (v4 or v6)
        # TODO ignore decom hosts
        for device in Device.objects.exclude(primary_ip4__isnull=True, primary_ip6__isnull=True):
            self.process_device_vm(device)

        for vm in VirtualMachine.objects.exclude(primary_ip4__isnull=True, primary_ip6__isnull=True):
            self.process_device_vm(vm)


        self.output.write('\n\n\n')  # Give me some space
        # And now create the groups
        for name, members in self.groups.items():
            self.output.write('{} = {}\n'.format(name, members.pop()))
            for member in members:
                self.output.write('{}\n'.format(member.rjust(len(name)+3)))

        return self.output.getvalue()
