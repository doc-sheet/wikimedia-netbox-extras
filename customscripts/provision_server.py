import csv
import io
import re

from string import ascii_lowercase

from django.core.exceptions import ObjectDoesNotExist

from dcim.choices import InterfaceTypeChoices
from dcim.models import Device, Interface
from extras.scripts import BooleanVar, ChoiceVar, FileVar, IntegerVar, ObjectVar, MultiObjectVar, Script, StringVar
from ipam.models import IPAddress, Prefix, VLAN

from _common import find_tor, format_logs, failures_in_log, port_to_iface, duplicate_cable_id, Importer

MGMT_IFACE_NAME = "mgmt"
PRIMARY_IFACE_NAME = "##PRIMARY##"
FRACK_TENANT_SLUG = "fr-tech"

CSV_HEADERS = ('device',
               'sw_port',
               'int_speed',
               'cable_id')

VLAN_DOMAINS = {
    'private': '{site}.wmnet',
    'public': 'wikimedia.org',
    'analytics': '{site}.wmnet',
    'cloud-private': 'private.{site}.wikimedia.cloud'
}

HOST_PROFILES = {
    'default': {
        'untagged_vlan': 'private1-{location}-{site}'
    },
    'alert': {
        'untagged_vlan': 'public1-{location}-{site}'
    },
    'aqs': {
        'untagged_vlan': 'private1-{location}-{site}',
        'cassandra_instances': 2
    },
    'bast': {
        'untagged_vlan': 'public1-{location}-{site}'
    },
    'cassandra-dev': {
        'untagged_vlan': 'private1-{location}-{site}',
        'cassandra_instances': 2
    },
    'cloudbackup': {
        'untagged_vlan': 'cloud-hosts1-{location}-{site}',
        'tagged_vlans': ['cloud-private-{location}-{site}']
    },
    'cloudcephosd': {
        'untagged_vlan': 'cloud-hosts1-{location}-{site}',
        'tagged_vlans': ['cloud-private-{location}-{site}']
    },
    'cloudcephmon': {
        'untagged_vlan': 'cloud-hosts1-{location}-{site}',
        'tagged_vlans': ['cloud-private-{location}-{site}']
    },
    'cloudcontrol': {
        'untagged_vlan': 'cloud-hosts1-{location}-{site}',
        'tagged_vlans': ['cloud-private-{location}-{site}']
    },
    'clouddb': {
        'untagged_vlan': 'private1-{location}-{site}',
        'ipv6_dns': False
    },
    'clouddumps': {
        'untagged_vlan': 'public1-{location}-{site}'
    },
    'cloudelastic': {
        'untagged_vlan': 'public1-{location}-{site}'
    },
    'cloudgw': {
        'untagged_vlan': 'cloud-hosts1-{location}-{site}',
        'tagged_vlans': ['cloud-private-{location}-{site}', 'cloud-gw-transport-{site}']
    },
    'cloudlb': {
        'untagged_vlan': 'cloud-hosts1-{location}-{site}',
        'tagged_vlans': ['cloud-private-{location}-{site}']
    },
    'cloudnet': {
        'untagged_vlan': 'cloud-hosts1-{location}-{site}',
        'tagged_vlans': ['cloud-private-{location}-{site}',
                         'cloud-instances2-b-{site}',
                         'cloud-instance-transport1-b-{site}'
                         ]
    },
    'cloudrabbit': {
        'untagged_vlan': 'cloud-hosts1-{location}-{site}',
        'tagged_vlans': ['cloud-private-{location}-{site}']
    },
    'cloudservice': {
        'untagged_vlan': 'cloud-hosts1-{location}-{site}',
        'tagged_vlans': ['cloud-private-{location}-{site}']
    },
    'cloudvirt': {
        'untagged_vlan': 'cloud-hosts1-{location}-{site}',
        'tagged_vlans': ['cloud-private-{location}-{site}', 'cloud-instances2-b-{site}']
    },
    'cloudvirtlocal': {
        'untagged_vlan': 'cloud-hosts1-{location}-{site}',
        'tagged_vlans': ['cloud-private-{location}-{site}', 'cloud-instances2-b-{site}']
    },
    'cloudvirt-wdqs': {
        'untagged_vlan': 'cloud-hosts1-{location}-{site}',
        'tagged_vlans': ['cloud-private-{location}-{site}', 'cloud-instances2-b-{site}']
    },
    'cloudweb': {
        'untagged_vlan': 'public1-{location}-{site}'
    },
    'contint': {
        'untagged_vlan': 'public1-{location}-{site}'
    },
    'db': {
        'untagged_vlan': 'private1-{location}-{site}',
        'ipv6_dns': False
    },
    'dbprov': {
        'untagged_vlan': 'private1-{location}-{site}',
        'ipv6_dns': False
    },
    'dbproxy': {
        'untagged_vlan': 'private1-{location}-{site}',
        'ipv6_dns': False
    },
    'dbstore': {
        'untagged_vlan': 'private1-{location}-{site}',
        'ipv6_dns': False
    },
    'dns': {
        'untagged_vlan': 'public1-{location}-{site}'
    },
    'dumpsdata': {
        'untagged_vlan': 'private1-{location}-{site}',
        'ipv6_dns': False
    },
    'ganeti': {
        'untagged_vlan': 'private1-{location}-{site}',
        'tagged_vlans': [
            'public1-{location}-{site}',
            'analytics1-{location}-{site}'
        ],
        'ipv6_dns': False,
        'no_ip_vlans': [
            'public1-{location}-{site}',
            'analytics1-{location}-{site}'
        ],
        'bridges': {
            'private': ['private1-{location}-{site}'],
            'public': ['public1-{location}-{site}'],
            'analytics': ['analytics1-{location}-{site}']
        }
    },
    'gerrit': {
        'untagged_vlan': 'public1-{location}-{site}'
    },
    'gitlab': {
        'untagged_vlan': 'public1-{location}-{site}'
    },
    'graphite': {
        'untagged_vlan': 'private1-{location}-{site}',
        'ipv6_dns': False
    },
    'maps': {
        'untagged_vlan': 'private1-{location}-{site}',
        'ipv6_dns': False
    },
    'mc': {
        'untagged_vlan': 'private1-{location}-{site}',
        'ipv6_dns': False
    },
    'mc-gp': {
        'untagged_vlan': 'private1-{location}-{site}',
        'ipv6_dns': False
    },
    'mirror': {
        'untagged_vlan': 'public1-{location}-{site}'
    },
    'ml-cache': {
        'untagged_vlan': 'private1-{location}-{site}',
        'cassandra_instances': 1
    },
    'ms-be': {
        'untagged_vlan': 'private1-{location}-{site}',
        'ipv6_dns': False
    },
    'mw': {
        'untagged_vlan': 'private1-{location}-{site}',
        'ipv6_dns': False
    },
    'mwlog': {
        'untagged_vlan': 'private1-{location}-{site}',
        'ipv6_dns': False
    },
    'ores': {
        'untagged_vlan': 'private1-{location}-{site}',
        'ipv6_dns': False
    },
    'parse': {
        'untagged_vlan': 'private1-{location}-{site}',
        'ipv6_dns': False
    },
    'pc': {
        'untagged_vlan': 'private1-{location}-{site}',
        'ipv6_dns': False
    },
    'netmon': {
        'untagged_vlan': 'public1-{location}-{site}'
    },
    'restbase': {
        'untagged_vlan': 'private1-{location}-{site}',
        'cassandra_instances': 3,
        'ipv6_dns': False
    },
    'sessionstore': {
        'untagged_vlan': 'private1-{location}-{site}',
        'cassandra_instances': 1,
        'ipv6_dns': False
    },
    'snapshot': {
        'untagged_vlan': 'private1-{location}-{site}',
        'ipv6_dns': False
    },
    'thanos-fe': {
        'untagged_vlan': 'private1-{location}-{site}',
        'ipv6_dns': False
    },
    'thumbor': {
        'untagged_vlan': 'private1-{location}-{site}',
        'ipv6_dns': False
    },
    'wdqs': {
        'untagged_vlan': 'private1-{location}-{site}',
        'ipv6_dns': False
    },
    'wtp': {
        'untagged_vlan': 'private1-{location}-{site}',
        'ipv6_dns': False
    }
}

TRIDENT3_DEVICES = (
    'qfx5120-48y-afi',
    'qfx5120-48y-afi2',
    'powerswitch-s5248f-on'
)


class ProvisionServerNetworkCSV(Script):

    class Meta:
        name = "CSV Server Provision"
        description = "Add multiple servers from CSV file, with vlans and parameters auto-assigned based on hostname."
        commit_default = False

    csv_file = FileVar(
        required=True,
        label="CSV import",
        description="See https://wikitech.wikimedia.org/wiki/Netbox_ProvisionServerNetwork_Script#CSV_Format for info"
    )

    def run(self, data, commit):
        reader = csv.DictReader(io.StringIO(data['csv_file'].read().decode('utf-8')))
        for row in reader:
            try:
                data = self._transform_csv(row)
            except csv.Error as e:
                self.log_failure(f"Error parsing CSV row {reader.line_num}: {e}")
                continue
            if not data:
                # If any issue with the transform (eg. typoed host), ignore the row
                continue
            provision_script = ProvisionServerNetwork()
            provision_script.provision_server_template(data)
            self.log.extend(provision_script.log)
            if failures_in_log(provision_script.log):
                self.log_failure(f"{data['device'].name}: Not provisioned due to failures")
            else:
                self.log_success(f"{data['device'].name}: Provisioned successfully")
        return format_logs(self.log)

    def _transform_csv(self, row):
        """ Transform and validate the CSV fields as needed """

        for header in CSV_HEADERS:
            try:
                row[header]
            except KeyError:
                self.log_failure(f"CSV header {header} missing, skipping.")
                return

        # Ensure that no cells are missing, not empty cells, but missing cells
        if any(value is None for value in row.values()):
            self.log_failure(f"{row['device']}: missing CSV cells, skipping.")
            return

        data = {}
        try:
            data['device'] = Device.objects.get(name=row['device'])
        except ObjectDoesNotExist:
            self.log_failure(f"{row['device']}: device not found, skipping.")
            return
        try:
            data['z_port'] = int(row['sw_port'])
        except ValueError:
            self.log_failure(f"{row['device']}: {row['sw_port']} is not a valid port number, skipping.")
            return

        server_phys = {
            "1G": InterfaceTypeChoices.TYPE_1GE_FIXED,
            "10G": InterfaceTypeChoices.TYPE_10GE_SFP_PLUS,
            "25G": InterfaceTypeChoices.TYPE_25GE_SFP28
        }
        if row['int_speed'].upper() not in server_phys:
            self.log_failure(f"{row['device']}: {row['int_speed']} not a valid speed (options are "
                             f"{', '.join(server_phys.keys())}), skipping.")
            return

        data['phy_type'] = server_phys[row['int_speed'].upper()]
        data['cable_id'] = row['cable_id']
        return data


class ProvisionServerNetwork(Script):

    class Meta:
        name = "Server Provision"
        description = "Add server switch connection, with vlans and other parameters auto-assigned based on hostname."
        commit_default = False

    device = ObjectVar(
        required=True,
        description=("REQUIRED: Inventory or planned server."),
        model=Device,
        query_params={
            'role': 'server',
            'status': ('inventory', 'planned'),
            'has_primary_ip': False,
        }
    )

    z_port = IntegerVar(label="Switch port",
                        description="REQUIRED: Physical port number (0-47 for Juniper, 1-48 for Dell)",
                        required=True,
                        min_value=0,
                        max_value=48)

    phy_type_choices = (
        (InterfaceTypeChoices.TYPE_1GE_FIXED, '1G'),
        (InterfaceTypeChoices.TYPE_10GE_SFP_PLUS, '10G'),
        (InterfaceTypeChoices.TYPE_25GE_SFP28, '25G')
    )
    phy_type = ChoiceVar(label="Interface type/speed",
                               description="REQUIRED: Ethernet link rate",
                               required=True,
                               choices=phy_type_choices)

    cable_id = StringVar(label="Cable ID",
                               description="OPTIONAL: Numeric reference matching label on physical cable",
                               required=False)

    def run(self, data, commit):
        """Run the script and return all the log messages."""
        self.log_info(f"Called with parameters: {data}")
        self.provision_server_template(data)
        return format_logs(self.log)

    def provision_server_template(self, data):
        """ Auto-assign attributes based on profile and pass all to ProvisionServerNetworkCustom """
        device = data['device']

        # Get hostname network profile
        hostname_prefix = re.sub('-dev$', '', device.name).rstrip('0123456789')
        try:
            net_profile = HOST_PROFILES[hostname_prefix]
            self.log_info(f"{device.name}: Found network profile for device type '{hostname_prefix}'")
        except KeyError:
            net_profile = HOST_PROFILES['default']
            self.log_info(f"{device.name}: Using default network profile as none defined for '{hostname_prefix}'")

        # Augment user-supplied data with elements specified in network profile
        data['untagged_vlan'] = self._render_vlan_templates([net_profile['untagged_vlan']], device)[0]
        if not data['untagged_vlan']:
            self.log_failure(f"{device.name}: Cannot provision host as no vlan found for primary interface.")
            return

        data['tagged_vlans'] = []
        if "tagged_vlans" in net_profile:
            data['tagged_vlans'] = self._render_vlan_templates(net_profile['tagged_vlans'], device)

        data['skip_ipv6_dns'] = False if "skip_ipv6_dns" not in net_profile else net_profile['skip_ipv6_dns']

        data['cassandra_instances'] = 0 if "cassandra_instances" not in net_profile \
            else net_profile['cassandra_instances']

        data['z_nbdevice'] = find_tor(device)
        if not data['z_nbdevice']:
            self.log_failure(f"{device.name}: Cannot find top-of-rack switch, check what is in rack")
            return

        data['no_ip_vlans'] = []
        if "no_ip_vlans" in net_profile:
            data['no_ip_vlans'] = self._render_vlan_templates(net_profile['no_ip_vlans'], device)

        # Run script to provision server with collected attributes
        provision_script = ProvisionServerNetworkCustom()
        provision_script.provision_server(data)
        self.log.extend(provision_script.log)

        if failures_in_log(provision_script.log):
            return

        # If bridges are defined in net profile process the vlan names
        bridges = {}
        if "bridges" in net_profile:
            for bridge_name, bridge_vlans in net_profile['bridges'].items():
                bridges[bridge_name] = self._render_vlan_templates(bridge_vlans, device)
        if bridges:
            self._add_host_bridges(bridges, device)

        return format_logs(self.log)

    def _render_vlan_templates(self, vlan_templates, device):
        """ Renders correct vlan names based on template for device and location

            Input:
                vlan_templates: list of vlan templates

            Returns: list of netbox vlan objects, after substituting for {site} and {location}
        """
        site = device.site.slug
        rack = device.rack.name.lower()

        nb_vlans = []
        for vlan_template in vlan_templates:
            location = rack
            vlan_name = vlan_template.format(location=location, site=site)
            try:
                # Get rack-specific vlan
                nb_vlans.append(VLAN.objects.get(name=vlan_name))
                self.log_info(f"{device.name}: Found vlan '{nb_vlans[-1].name}' from template {vlan_template}")
            except ObjectDoesNotExist:
                # Rack-specific vlan doesn't exist, look for row-specific
                location = rack[0]
                vlan_name = vlan_template.format(location=location, site=site)
                try:
                    nb_vlans.append(VLAN.objects.get(name=vlan_name))
                    self.log_info(f"{device.name}: Found vlan '{nb_vlans[-1].name}' from template {vlan_template}")
                except ObjectDoesNotExist:
                    # Row-specific vlan doesn't exist, look for site-wide
                    vlan_name = vlan_template.format(location='', site=site).replace('--', '-')
                    try:
                        nb_vlans.append(VLAN.objects.get(name=vlan_name))
                        self.log_info(f"{device.name}: Found vlan '{nb_vlans[-1].name}' from template {vlan_template}")
                    except ObjectDoesNotExist:
                        self.log_warning(f"{device.name}: Failed to find vlan from template '{vlan_template}' "
                                         f"in {device.site.slug.upper()} rack {rack.upper()}")
        return nb_vlans

    def _add_host_bridges(self, bridges, device):
        """ Creates bridge interfaces on host based on data from network template """

        ifaces = device.interfaces.all()
        for bridge_name, bridge_members in bridges.items():
            # Create the bridge interface
            nb_bridge = Interface(name=bridge_name, device=device, type='bridge', mgmt_only=False)
            nb_bridge.save()
            self.log_success(f"{device.name}: Added bridge device {bridge_name}")
            for member_vlan in bridge_members:
                member_iface = None
                # Find device interface with this vlan set as untagged
                for iface in ifaces:
                    if iface.untagged_vlan and iface.untagged_vlan == member_vlan:
                        member_iface = iface
                        break
                if member_iface is None:
                    self.log_warning(f"{device.name}: has no interface on vlan {member_vlan} to "
                                     f"bind to bridge {bridge_name}")
                    continue
                member_iface.bridge = nb_bridge
                member_iface.save()
                self.log_success(f"{device.name}: set interface {member_iface.name} master "
                                 f"device to bridge {bridge_name}")
                # If member port has IPs on it move them to bridge device
                if iface.count_ipaddresses > 0:
                    self._move_interface_ips(iface, nb_bridge)

            if nb_bridge.bridge_interfaces.count() == 0:
                # No matching vlans were added (for instance on analytics bridge outside eqiad) - delete it
                nb_bridge.delete()
                self.log_warning(f"{device.name}: Removed bridge device {bridge_name} as config rendered no members")

    def _move_interface_ips(self, old_int, new_int):
        """ Moves all IP addresses from old_int to new_int """
        addresses = IPAddress.objects.filter(interface=old_int)
        for address in addresses:
            address.assigned_object = new_int
            address.save()
            self.log_success(f"{new_int.device.name}: moved IP {address} from {old_int.name} to {new_int.name}")


class ProvisionServerNetworkCustom(Script, Importer):

    class Meta:
        name = "Custom Server Provision"
        description = "Add server switch connection, manually specifying vlans and other parameters."
        commit_default = False

    device = ObjectVar(
        required=True,
        description=("REQUIRED: Inventory or planned server."),
        model=Device,
        query_params={
            'role': 'server',
            'status': ('inventory', 'planned'),
            'has_primary_ip': False,
        }
    )

    z_port = IntegerVar(label="Switch port",
                        description="REQUIRED: Physical port number (0-47 for Juniper, 1-48 for Dell)",
                        required=True,
                        min_value=0,
                        max_value=48)

    phy_type_choices = (
        (InterfaceTypeChoices.TYPE_1GE_FIXED, '1G'),
        (InterfaceTypeChoices.TYPE_10GE_SFP_PLUS, '10G'),
        (InterfaceTypeChoices.TYPE_25GE_SFP28, '25G')
    )
    phy_type = ChoiceVar(label="Interface type/speed",
                               description="REQUIRED: Ethernet link rate",
                               required=True,
                               choices=phy_type_choices)

    untagged_vlan = ObjectVar(
        required=True,
        label="Untagged VLAN",
        description=("REQUIRED: Select the primary vlan the device will connect on, this is untagged on the wire."),
        model=VLAN,
        query_params={
            "group": "production",
            "status": "active"
        }
    )

    z_nbdevice = ObjectVar(
        label="Switch",
        required=False,
        description=("OPTIONAL: Top of rack switch, if not set will try to find it automatically."),
        model=Device,
        query_params={'role': ('asw', 'cloudsw'), 'status': ('active', 'planned'), 'name__nisw': 'ssw'}
    )

    cable_id = StringVar(label="Cable ID",
                               description="OPTIONAL: Numeric reference matching label on physical cable",
                               required=False)

    tagged_vlans = MultiObjectVar(
        required=False,
        label="Tagged VLANs",
        description="OPTIONAL: One or more additional vlans to be configured as sub-interfaces on the host and "
                    "trunked from switch",
        model=VLAN,
        query_params={
            "group": "production",
            "status": "active"
        }
    )

    skip_ipv6_dns = BooleanVar(
        required=False,
        label="Skip IPv6 DNS records.",
        description=("OPTIONAL: Skip the generation of the IPv6 DNS records. Enable if the devices don't yet fully "
                     "support IPv6."),
    )

    cassandra_instances = ChoiceVar(
        required=False,
        choices=[(i, i) for i in range(6)],
        label="How many Cassandra instances",
        description=("OPTIONAL: To be set only for hosts that will run Cassandra. This many additional IPv4s will be "
                     "allocated and their DNS name will be set to $HOSTNAME-a, $HOSTNAME-b, etc."),
    )

    no_ip_vlans = MultiObjectVar(
        required=False,
        label="No IP Vlans",
        description="OPTIONAL: One or more vlans included in tagged_vlans, which the host connects to "
                    "but doesn't need an IP",
        model=VLAN,
        query_params={
            "group": "production",
            "status": "active"
        }
    )

    def run(self, data, commit):
        """Run the script and return all the log messages."""
        self.log_info(f"Called with parameters: {data}")
        self.provision_server(data)
        return format_logs(self.log)

    def provision_server(self, data):
        """Process a single device."""
        device = data['device']
        z_port = data['z_port']
        phy_type = data['phy_type']
        cable_id = data['cable_id']
        untagged_vlan = data['untagged_vlan']
        tagged_vlans = data['tagged_vlans']
        skip_ipv6_dns = data['skip_ipv6_dns']
        cassandra_instances = 0 if data['cassandra_instances'] == '' else int(data['cassandra_instances'])
        z_nbdevice = data['z_nbdevice']
        no_ip_vlans = data['no_ip_vlans']
        assign_mgmt = True

        if not device.rack:
            self.log_failure(f"{device.name}: missing rack information.")
            return False

        if cable_id and duplicate_cable_id(cable_id, device.site):
            self.log_failure(f"{device.name}: Cable ID {cable_id} already assigned in {device.site.slug}.")
            return False

        if not z_nbdevice:
            z_nbdevice = find_tor(device)
            if not z_nbdevice:
                self.log_failure(f"{device.name}: Can't find an adequate top-of-rack switch in rack {device.rack}. "
                                 "please double check or select it manually.")
                return False

        if z_port > 48:
            self.log_failure(f"{device.name}: {z_port} is an invalid port number (must be lower than 48)")
            return

        if self._invalid_trident3_port(z_nbdevice, z_port, phy_type):
            return

        # Get logical switch interface name based on platform, port num and phy type
        z_iface_name = port_to_iface(z_port, z_nbdevice, phy_type)
        if not z_iface_name:
            self.log_failure(f"{device.name}: Could not provision port {z_port} at speed "
                             f"{phy_type.split('base')[0].upper()} on {z_nbdevice.name} - check this is valid")
            return
        else:
            self.log_info(f"{device.name}: Interface name {z_iface_name} found based on physical port {z_port}")

        ifaces = device.interfaces.all()
        if ifaces:
            # If interfaces exist, but only the mgmt, proceed but skip mgmt addition
            if (len(ifaces) == 1 and ifaces[0].name == MGMT_IFACE_NAME and ifaces[0].count_ipaddresses == 1
                    and ifaces[0].ip_addresses.all()[0].dns_name):
                self.log_warning(f"{device.name}: Skipping assignment of MGMT interface because already allocated")
                assign_mgmt = False
            else:
                # Other interfaces exist / host is not in normal state for new device, skip
                ifaces_list = ", ".join(i.name for i in ifaces)
                self.log_failure(f"{device.name}: interfaces already defined: {ifaces_list}, skipping.")
                return

        # Create MGMT interface and assign IP if required
        if assign_mgmt:
            self._assign_mgmt(device)

        # For fundraising we add nothing other than the mgmt interface in Netbox
        if device.tenant is not None and device.tenant.slug == FRACK_TENANT_SLUG:
            self.log_warning(f"{device.name}: Skipping any other changes as Fundraising Tech IPs and connections "
                             "are handled outside of Netbox")
            return

        # Create host interfaces
        nbiface, vlan_ints = self._create_host_ints(device, phy_type, untagged_vlan, tagged_vlans)

        # Assign host IP addresses and bind to interfaces
        self._assign_int_ips(nbiface, skip_ipv6_dns, cassandra_instances)
        for vlan_int in vlan_ints:
            if vlan_int.untagged_vlan in no_ip_vlans:
                self.log_info(f"{device.name}: Skipping IP assignment on {vlan_int.name} "
                              f"({vlan_int.untagged_vlan.name}) as per device network profile")
            else:
                self._assign_int_ips(vlan_int, skip_ipv6_dns, pri_dns=nbiface.device.primary_ip4.dns_name)

        self._configure_switch_link(nbiface, z_nbdevice, z_iface_name, phy_type, cable_id)

    def _configure_switch_link(self, nbiface, z_nbdevice, z_iface_name, phy_type, cable_id):
        """ Adds link from nbiface to z_port on z_nbdevice, plus configures switch port vlans """

        # Create interface with same vlan config as host
        z_nbiface = self._update_z_nbiface(z_nbdevice, z_iface_name, nbiface.untagged_vlan,
                                           tagged_vlans=list(nbiface.tagged_vlans.all()))

        # And cable between switch and host if switch has none already
        if z_nbiface.cable:
            self.log_warning(f"{nbiface.device.name}: There is already a cable on {z_nbiface.device}:"
                             f"{z_nbiface} (typo?), skipping cable creation, please do it manually")
            return
        self._create_cable(nbiface, z_nbiface, label=cable_id if cable_id else '')

    def _create_host_ints(self, device, phy_type, untagged_vlan, tagged_vlans):
        """Creates primary interface on device and any vlan sub-interfaces

           Returns primary interface object"""

        iface = self._add_iface(PRIMARY_IFACE_NAME, device, phy_type, untagged_vlan=untagged_vlan,
                                tagged_vlans=tagged_vlans, mgmt=False)

        vlan_ints = []
        if tagged_vlans is not None:
            for vlan in tagged_vlans:
                vint = self._add_iface(f"vlan{vlan.vid}", device, 'virtual', untagged_vlan=vlan,
                                       mgmt=False, parent=iface)
                vlan_ints.append(vint)

        return iface, vlan_ints

    def _assign_int_ips(self, iface, skip_ipv6_dns, cassandra_instances=0, pri_dns=None):
        """ Assigns one or more IPs to a NB interface, usually one v4 and one v6,
            but will add additional for each cassandra_instnaces > 0 """
        vlan = iface.untagged_vlan
        prefixes_v4 = vlan.prefixes.filter(prefix__family=4, status="active")  # Must always be one
        prefixes_v6 = vlan.prefixes.filter(prefix__family=6, status="active")  # Can either be one or not exists

        if len(prefixes_v4) != 1 or len(prefixes_v6) > 1:
            self.log_warning(f"{iface.device.name}: unsupported case, found {len(prefixes_v4)} v4 prefixes and "
                             f"{len(prefixes_v6)} v6 prefixes, expected 1 and 0 or 1 respectively, "
                             "skipping IP allocation.")
            return

        prefix_v4 = prefixes_v4[0]
        prefix_v6 = None
        if prefixes_v6:
            prefix_v6 = prefixes_v6[0]

        self.log_info(f"{iface.device.name}: selecting address from prefix {prefix_v4.prefix} ({vlan.name})")

        ip_address = prefix_v4.get_first_available_ip()
        if ip_address is None:
            self.log_warning(f"{iface.device.name}: unable to find an available IP in prefix "
                             f"{prefix_v4.prefix}, skipping IP allocation.")
            return

        # Get domain suffix to use based on vlan name
        dns_name = ''
        for vlan_type, domain_template in VLAN_DOMAINS.items():
            if vlan.name.startswith(vlan_type):
                dns_suffix = domain_template.format(site=iface.device.site.slug)
                dns_name = f"{iface.device.name}.{dns_suffix}"
                break

        if not dns_name:
            self.log_warning(f"{iface.device.name}: No DNS suffix matching vlan name {vlan.name} "
                             "- skipping DNS record generation")

        # Prefix dns_name with vlan int name if needed
        # i.e. if it would otherwise duplicate the primary dns_name (this doesn't trigger for any current hosts)
        if dns_name == pri_dns:
            dns_name = f"{iface.name}.{dns_name}"

        ipv4 = self._add_ip(ip_address, dns_name, prefix_v4, iface, iface.device)
        if iface.type != 'virtual':
            iface.device.primary_ip4 = ipv4
            iface.device.save()
            self.log_success(f"{iface.device.name}: marked IPv4 address {ipv4} as primary IPv4 for device.")

        # Allocate additional IPs for cassandra instances - we only do this for v4
        for letter in ascii_lowercase[:cassandra_instances]:
            extra_ip_address = prefix_v4.get_first_available_ip()
            extra_dns_name = ''
            if dns_name:
                extra_dns_name = f"{iface.device.name}-{letter}.{dns_suffix}"
            self._add_ip(extra_ip_address, extra_dns_name, prefix_v4, iface, iface.device)

        if prefix_v6 is None:
            self.log_warning(f"{iface.device.name}: no IPv6 prefix found for VLAN {vlan.name}, "
                             "skipping IPv6 allocation.")
            return

        dns_name_v6 = dns_name
        if skip_ipv6_dns:
            self.log_info(f"{iface.device.name}: Not assigning DNS name to the IPv6 address as requested")
            dns_name_v6 = ""

        # Generate the IPv6 address embedding the IPv4 address, for example from an IPv4 address 10.0.0.1 and an
        # IPv6 prefix 2001:db8:3c4d:15::/64 the mapped IPv6 address 2001:db8:3c4d:15:10:0:0:1/64 is generated.
        prefix_v6_base, prefix_v6_mask = str(prefix_v6).split("/")
        mapped_v4 = str(ipv4).split('/')[0].replace(".", ":")
        ipv6_address = f"{prefix_v6_base.rstrip(':')}:{mapped_v4}/{prefix_v6_mask}"
        ipv6 = self._add_ip(ipv6_address, dns_name_v6, prefix_v6, iface, iface.device)
        if iface.type != 'virtual':
            iface.device.primary_ip6 = ipv6
            iface.device.save()
            self.log_success(f"{iface.device.name}: marked IPv6 address {ipv6} as primary IPv6.")

    def _assign_mgmt(self, device):
        """Create a management interface in the device and assign to it a management IP."""
        iface_type = InterfaceTypeChoices.TYPE_1GE_FIXED
        iface = self._add_iface(MGMT_IFACE_NAME, device, iface_type, mgmt=True)

        # determine prefix appropriate to site of device
        try:
            prefix = Prefix.objects.get(
                site=device.site, role__slug="management", tenant=device.tenant, status="active"
            )
        except ObjectDoesNotExist:
            self.log_failure(f"{device.name}: can't find management prefix for site {device.site.slug}.")
            return

        self.log_debug(f"{device.name}: selecting mgmt address from prefix {prefix.prefix}")
        ip_address = prefix.get_first_available_ip()
        if ip_address is None:
            self.log_failure(f"{device.name}: unable to find an available IP in prefix {prefix.prefix}")
            return

        if device.tenant and device.tenant.slug == FRACK_TENANT_SLUG:
            dns_name = f"{device.name}.mgmt.frack.{device.site.slug}.wmnet"
        else:
            dns_name = f"{device.name}.mgmt.{device.site.slug}.wmnet"

        self._add_ip(ip_address, dns_name, prefix, iface, device)

    def _add_iface(self, name, device, iface_type, untagged_vlan=None, tagged_vlans=None, mgmt=False, parent=None):
        """Add an interface to the device, set vlans and parent if passed."""
        iface = Interface(name=name, mgmt_only=mgmt, device=device, type=iface_type)
        if tagged_vlans:
            iface.mode = 'tagged'
            iface.save()
            iface.tagged_vlans.set([vlan.id for vlan in tagged_vlans])
        if untagged_vlan:
            if not iface.mode:
                iface.mode = 'access'
                iface.save()
            iface.untagged_vlan = untagged_vlan
        if parent is not None:
            iface.parent = parent
        iface.save()
        self.log_success(f"{device.name}: created interface {name}")
        return iface

    def _add_ip(self, address, dns_name, prefix, iface, device):
        """Assign an IP address to the interface."""
        address = IPAddress(
            address=address,
            status="active",
            assigned_object=iface,
            tenant=device.tenant
        )
        if dns_name:
            address.dns_name = dns_name
        address.save()
        self.log_success(f"{device.name}: assigned IPv{prefix.family} {address} to interface {iface.name} "
                         f"with DNS name '{dns_name}'.")

        return address

    def _invalid_trident3_port(self, z_nbdevice, z_port, phy_type):
        """ Checks that the port speed is consistent with others in block due to Trident 3 constarint"""
        if z_nbdevice.device_type.slug not in TRIDENT3_DEVICES:
            return False

        logical_port = z_port
        if z_nbdevice.device_type.manufacturer.slug == 'dell':
            logical_port = z_port - 1

        block_start = logical_port - (logical_port % 4)
        block_ports = range(block_start, block_start + 4)

        device_ints = Interface.objects.filter(device_id=z_nbdevice.id, enabled=True,
                                               mgmt_only=False).exclude(type='virtual')
        for device_int in device_ints:
            # Get logical port number from interface name
            try:
                port_num = int(device_int.name.replace('Ethernet', '').split('/')[-1])
            except ValueError:
                self.log_failure(f"{z_nbdevice.name}: Couldn't get port number from interface name {device_int.name}")
                return True

            if port_num in block_ports and device_int.type != phy_type:
                self.log_failure(f"{z_nbdevice.name}: Cannot set port {z_port} up as '{phy_type}' as interface "
                                 f"{device_int.name} is in the same block and has type '{device_int.type}'")
                return True

        return False
