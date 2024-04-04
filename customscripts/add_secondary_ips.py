from _common import Importer, format_logs

from dcim.models import Device
from ipam.models import IPAddress, Prefix
from extras.scripts import ChoiceVar, ObjectVar, Script

from string import ascii_lowercase


class AddSecondaryIPs(Script, Importer):
    class Meta:
        name = "Assign additional IPv4 addresses to host"
        description = "Assign's additional IPv4 addresses to a host's primary int (principally for Cassandra instances)"
        commit_default = True

    device = ObjectVar(
        required=True,
        description=("Server. (Required)"),
        model=Device,
        query_params={
            'role': 'server',
            'has_primary_ip': True,
        }
    )

    additional_ips = ChoiceVar(
        required=True,
        choices=[(i, i) for i in range(6)],
        label="How many additional IPs to assign",
        description=("This many additional IPv4s will be allocated and their DNS name "
                     "will be set to $HOSTNAME-a, $HOSTNAME-b, etc.")
    )

    def run(self, data, commit):
        """Run the script and return all the log messages."""
        self.log_info(f"Called with parameters: {data}")
        device = data['device']

        # Get existing primary interface IPv4 addresses
        nb_primary_int = device.primary_ip4.assigned_object
        interface_ips = IPAddress.objects.filter(interface=nb_primary_int.id)
        interface_ip4s = [interface_ip for interface_ip in interface_ips if interface_ip.family == 4]

        # Get primary IP netbox prefix
        primary_ip4 = device.primary_ip4.address
        prefix_v4 = Prefix.objects.get(prefix=f'{primary_ip4.network}/{primary_ip4.prefixlen}')

        # Get indexes of letters to use for additional dns names and suffix
        first_letter_idx = len(interface_ip4s) - 1
        last_letter_idx = first_letter_idx + int(data['additional_ips'])
        dns_suffix = device.primary_ip4.dns_name.lstrip(device.name)

        # Allocate additional IPs
        for letter in ascii_lowercase[first_letter_idx:last_letter_idx]:
            ip_address = prefix_v4.get_first_available_ip()
            dns_name = f"{device.name}-{letter}{dns_suffix}"
            # Create IP object
            new_addr = IPAddress(
                address=ip_address,
                status="active",
                dns_name=dns_name,
                assigned_object=nb_primary_int
            )
            self.log_success(f"{device}: assigned additional address {new_addr} to interface {nb_primary_int} "
                             f"with DNS name '{dns_name}'")

        self.log_success("Additional IPs added, please run sre.dns.netbox cookbook to update DNS records")
        return format_logs(self.log)
