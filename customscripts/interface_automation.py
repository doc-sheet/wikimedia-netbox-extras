import configparser
import requests
import ipaddress

from django.core.exceptions import ObjectDoesNotExist

from dcim.choices import InterfaceTypeChoices
from dcim.models import Device, Interface
from ipam.choices import IPAddressStatusChoices
from ipam.models import Prefix, IPAddress
from extras.scripts import Script, StringVar
from virtualization.models import VirtualMachine

CONFIGFILE = "/etc/netbox/reports.cfg"


class ImportPuppetInterfaces(Script):
    class Meta:
        name = "Import Interfaces from PuppetDB"
        description = "Access PuppetDB and import host(s) interface data."

    device = StringVar(description="The device name(s) to import interface(s) for (space separated)")

    @staticmethod
    def _get_ipv6_prefix_length(ipv6mask):
        """Convert an old-style IPv6 netmask into a prefix length in bits.

        Arguments:
            ipv6mask (str): An IPv6 netmask

        Returns:
            int: The number of network prefix bits contained in the netmask.

        """
        counts = [
            0,
            0x8000,
            0xC000,
            0xE000,
            0xF000,
            0xF800,
            0xFC00,
            0xFE00,
            0xFF00,
            0xFF80,
            0xFFC0,
            0xFFE0,
            0xFFF0,
            0xFFF8,
            0xFFFC,
            0xFFFE,
            0xFFFF,
        ]

        length = 0

        for chunk in ipv6mask.split(":"):
            if not chunk:
                break

            chunk_int = int(chunk, 16)
            if chunk_int == 0:
                break

            length += counts.index(chunk_int)

        return length

    def _assign_ip_to_interface(self, nbiface, networking, iface, iface_dict, is_ipv6):
        """Perform the minor complexity of assigning an IP address to an interface as specified by
           a PuppetDB interface fact."""
        if is_ipv6:
            network_bits = self._get_ipv6_prefix_length(iface_dict["netmask6"])
            address = f"{iface_dict['ip6']}/{network_bits}"
        else:
            network_bits = ipaddress.ip_network(f"0.0.0.0/{iface_dict['netmask']}").prefixlen
            address = f"{iface_dict['ip']}/{network_bits}"

        try:
            addr = IPAddress.objects.get(address=address)
        except ObjectDoesNotExist:
            self.log_info(f"Created {address}")
            if is_ipv6:
                addr = IPAddress(family=6, address=address, interface=nbiface)
            else:
                addr = IPAddress(family=4, address=address, interface=nbiface)
            addr.save()

        if addr.interface:
            if addr.interface.virtual_machine:
                olddev = addr.interface.virtual_machine
            else:
                olddev = addr.interface.device
        else:
            olddev = None

        if nbiface.virtual_machine:
            newdev = nbiface.virtual_machine
        else:
            newdev = nbiface.device

        if not addr.interface:
            # scenario 0, no interface assigned
            self.log_info(f"Assigning {address} to {newdev}:{nbiface}")
        elif olddev != newdev:
            # scenario 1, interface is not the interface in question
            if addr.interface.id != nbiface.id:
                # scenario 1a, interface does not belong to this device
                self.log_info(f"Taking IP address {addr} from {olddev}:{addr.interface}")
                self.log_info(f"Assigning {address} to {newdev}:{nbiface}")
            else:
                # scenario 1b, interface does belong to this device
                if addr.interface.name not in networking["interfaces"]:
                    # scenario 1b-1 basically renaming a device so we need to copy the description field
                    nbiface.description = addr.interface.description
                    nbiface.save()

        # finally actually reassign interface
        addr.interface = nbiface

        if iface == networking["primary"]:
            self.log_info(f"Adding FQDN {networking['fqdn']} to {addr}")
            addr.dns_name = networking["fqdn"]
            addr.is_primary = True
            self.log_info(f"Setting {nbiface} as primary for {newdev}")
            if is_ipv6:
                newdev.primary_ip6 = addr
            else:
                newdev.primary_ip4 = addr
            newdev.save()
        addr.save()
        return addr

    def _import_interfaces_for_device(self, device, cfg, is_virtual=False):
        """Import one device's interfaces from PuppetDB based on specified configuration."""
        # Get networking facts
        puppetdb_url = "/".join([cfg["puppetdb"]["url"], "/v1/facts", "{}", device.name])
        response = requests.get(puppetdb_url.format("networking"), verify=cfg["puppetdb"]["ca_cert"])
        if response.status_code != 200:
            message = f"Cannot retrieve PuppetDB `networking` facts about {device.name}"
            self.log_error(message)
            return message
        networking = response.json()
        # Get net_driver facts
        response = requests.get(puppetdb_url.format("net_driver"), verify=cfg["puppetdb"]["ca_cert"])
        if response.status_code != 200:
            message = f"Cannot retrieve PuppetDB `net_driver` facts about {device.name}"
            self.log_error(message)
            return message
        net_driver = response.json()

        for iface, iface_dict in networking["interfaces"].items():
            try:
                nbiface = device.interfaces.get(name=iface)
            except ObjectDoesNotExist:
                self.log_info(f"Creating interface {iface} for device {device}".format(iface, device))
                iface_fmt = InterfaceTypeChoices.TYPE_1GE_FIXED
                if iface in net_driver:
                    if net_driver[iface]["speed"] == 10000:
                        iface_fmt = InterfaceTypeChoices.TYPE_10GE_SFP_PLUS
                if is_virtual:
                    nbiface = Interface(name=iface,
                                        mgmt_only=False,
                                        virtual_machine=device,
                                        type=iface_fmt,
                                        mtu=iface_dict["mtu"])
                else:
                    nbiface = Interface(name=iface,
                                        mgmt_only=False,
                                        device=device,
                                        type=iface_fmt,
                                        mtu=iface_dict["mtu"])
                nbiface.save()
            if iface == 'lo':
                # we can't assign loopback address more than once so we'll just skip it.
                continue
            if "ip" in iface_dict:
                # process ipv4 address
                addr = self._assign_ip_to_interface(nbiface, networking, iface, iface_dict, False)
            if "ip6" in iface_dict:
                # process ipv6 address
                self._assign_ip_to_interface(nbiface, networking, iface, iface_dict, True)

        return f"{device.name} finished."

    def run(self, data, commit):
        cfg = configparser.ConfigParser()
        cfg.read(CONFIGFILE)

        devices = Device.objects.filter(name__in=data['device'].split())
        vmdevices = VirtualMachine.objects.filter(name__in=data['device'].split())
        messages = []
        if not devices and not vmdevices:
            message = "No devices found for specified list."
            self.log_failure(message)
            return message

        for device in devices:
            self.log_info(f"Processing device {device}")
            messages.append(self._import_interfaces_for_device(device, cfg))

        for device in vmdevices:
            self.log_info(f"Processing virtual device {device}")
            messages.append(self._import_interfaces_for_device(device, cfg, True))

        return "\n".join(messages)


class CreateManagementInterface(Script):
    class Meta:
        name = "Create management interface"
        description = "Create a management interface for specified device(s) and assign an IP address(es)."

    device = StringVar(
        description="Device name(s) to add management interface(s) to (space separated)",
    )

    def _add_ip_to_interface(self, device, interface):
        """Create an IP address for a device if appropriate from the correct prefix."""
        if interface.ip_addresses.all():
            message = "refusing to create additional IP address for mgmt on {}".format(device.name)
            self.log_info(message)
            return message

        # determine prefix appropriate to site of device
        try:
            prefix = Prefix.objects.get(site=device.site, role__slug="management", tenant=device.tenant)
        except ObjectDoesNotExist:
            message = "Can't find prefix for site {} on device {}".format(device.site.slug, device.name)
            self.log_failure(message)
            return message
        self.log_info("Selecting address from network {}".format(prefix.prefix))
        available_ips = iter(prefix.get_available_ips())

        # disable 0net skipping on frack
        if device.tenant and device.tenant.slug == 'fr-tech':
            zeroth_net = None
        else:
            # skip the first /24 net as this is reserved for network devices
            zeroth_net = list(ipaddress.ip_network(prefix.prefix).subnets(new_prefix=24))[0]

        ip = None
        for ip in available_ips:
            address = ipaddress.ip_address(ip)
            if zeroth_net is None or address not in zeroth_net:
                break
            else:
                ip = None

        if ip:
            # create IP address as child of appropriate prefix
            newip = IPAddress(
                address="{}/{}".format(ip, prefix.prefix.prefixlen),
                status=IPAddressStatusChoices.STATUS_ACTIVE,
                family=prefix.family,
            )
            # save ASAP
            newip.save()
            newip.vrf = prefix.vrf.pk if prefix.vrf else None
            # assign ip to interface
            newip.interface = interface
            newip.tenant = device.tenant
            newip.save()

            message = "Created IP {} for mgmt on device {}".format(newip, device.name)
            self.log_success(message)
            return message

        # fall through to failure
        message = "Not enough IPs to allocate one on prefix {}".format(prefix.prefix)
        self.log_failure(message)
        return message

    def run(self, data):
        """Create a 'mgmt' interface, and, if requested, allocate an appropriate IP address."""
        devices = Device.objects.filter(name__in=data['device'].split())
        messages = []
        if not devices:
            message = "No devices found for specified list."
            self.log_failure(message)
            return message

        for device in devices:
            self.log_info("processing device {}".format(device.name))
            try:
                mgmt = device.interfaces.get(name='mgmt')
                self.log_info("mgmt already exists for device {}".format(device.name))
            except ObjectDoesNotExist:
                # create interface of name mgmt, is_mgmt flag set of type 1G Ethernet
                mgmt = Interface(name="mgmt", mgmt_only=True, device=device, type=InterfaceTypeChoices.TYPE_1GE_FIXED)
                mgmt.save()

            messages.append(self._add_ip_to_interface(device, mgmt))

        return "\n".join(messages)
