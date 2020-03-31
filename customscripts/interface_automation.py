import configparser
import ipaddress
import requests

from dns import resolver, reversename, rrset
from dns.exception import DNSException
from dns.name import Name

from django.core.exceptions import ObjectDoesNotExist

from dcim.choices import InterfaceTypeChoices
from dcim.models import Device, Interface
from extras.scripts import Script, StringVar
from ipam.choices import IPAddressStatusChoices
from ipam.models import Prefix, IPAddress
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

        This is provided because the ipaddress library does not support this (deprecated) method
        of specifying the host bits, however this is how PuppetDB provides the information.

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

    def _resolve_address(self, qname, rrtype):
        """Perform a DNS resolution on an A or AAA record.

        Returns empty list on no results.
        """
        results = []
        try:
            result = resolver.query(qname, rrtype)
            for res in result.rrset:
                results.append(res.address)
        except (resolver.NoAnswer, resolver.NXDOMAIN):
            self.log_debug(f"[DNS] Record {rrtype} not found for {qname}")
        except DNSException:
            self.log_debug(f"[DNS] Cannot resolve {rrtype} for {qname}")
        return results

    def _resolve_ipv6(self, dnsname):
        return self._resolve_address(dnsname, "AAAA")

    def _resolve_ipv4(self, dnsname):
        return self._resolve_address(dnsname, "A")

    def _resolve_ptr(self, ipaddr):
        """Perform a DNS PTR resolution.

        Returns empty None if there is no result."""
        try:
            result = resolver.query(ipaddr, "PTR")
            results = [r.address.strip('.') for r in result.rrset]
            return results[0]
        except (resolver.NoAnswer, resolver.NXDOMAIN):
            self.log_debug(f"[DNS] PTR record not found for {ipaddr}")
        except DNSException as e:
            self.log_debug(f"[DNS] PTR cannot resolve {ipaddr}")
        return None


    def _assign_name(self, ipaddr, networking, is_ipv6):
        """Possibly assign name to address if the conditions are right. Otherwise,
           return a message array explaining why we didn't."""

        # leave it alone if it's already assigned to the right name
        if (ipaddr.dns_name == networking["fqdn"]):
            self.log_debug(f"{networking['fqdn']} assign_name: {ipaddr.address} already has correct DNS name.")
            return []

        # leave it alone if it's assigned another name, but warn as this requires human intervention
        if (ipaddr.dns_name):
            self.log_failure((f"{networking['fqdn']} assign_name: {ipaddr.address} has a different DNS name than"
                              f"expected: {ipaddr.dns_name}"))
            return []

        # consider ipaddress for DNS name
        # FIXME: Note that the DNS logic is transition period
        # temporary to shake out any unassigned IP addresses and similar.
        output = []
        nogo = True
        if (is_ipv6):
            ipv6 = self._resolve_ipv6(networking["fqdn"])
            ptr = self._resolve_ptr(ipaddr.address)
            if (not ipv6) and (not ptr):
                self.log_warning(f"{networking['fqdn']} assign_name: No IPv6 DNS records.")
                output.append(f"{networking['fqdn']} assign_name: No IPv6 DNS records.")
            elif (not ipv6):
                self.log_warning(f"{networking['fqdn']} assign_name: No IPv6 AAA records.")
                output.append(f"{networking['fqdn']} assign_name: No IPv6 AAA records.")
            elif (not ptr):
                self.log_warning(f"{networking['fqdn']} assign_name: No IPv6 PTR record.")
                output.append(f"{networking['fqdn']} assign_name: No IPv6 PTR record.")
            else:
                nogo = False
        else:
            ipv4 = self._resolve_ipv4(networking["fqdn"])
            ptr = self._resolve_ptr(ipaddr.address)
            if (not ipv4) and (not ptr):
                self.log_warning(f"{networking['fqdn']} assign_name: No IPv4 DNS records.")
                output.append(f"{networking['fqdn']} assign_name: No IPv4 DNS records.")
            elif (not ipv4):
                self.log_warning(f"{networking['fqdn']} assign_name: No IPv4 A records.")
                output.append(f"{networking['fqdn']} assign_name: No IPv4 A records.")
            elif (not ptr):
                self.log_warning(f"{networking['fqdn']} assign_name: No IPv4 PTR record.")
                output.append(f"{networking['fqdn']} assign_name: No IPv4 PTR record.")
            else:
                nogo = False

        if not nogo:
            self.log_info(f"Adding FQDN {networking['fqdn']} to {ipaddr}")
            ipaddr.dns_name = networking["fqdn"]

        return output

    def _assign_ip_to_interface(self, nbiface, networking, iface, iface_dict, is_ipv6, is_vdev):
        """Perform the minor complexity of assigning an IP address to an interface as specified by
           a PuppetDB interface fact."""
        output = []
        if is_ipv6:
            # we need to translate the netmask6 exposed by puppet into a prefix length since ipaddress
            # library does not support this case.
            nm = self._get_ipv6_prefix_length(iface_dict["netmask6"])
            addr = iface_dict["ip6"]
        else:
            nm = iface_dict["netmask"]
            addr = iface_dict["ip"]

        address = ipaddress.ip_interface(f"{addr}/{nm}")

        # check for skippable classes of addresses (loopback and link local)
        if address.is_loopback or address.is_link_local:
            return []

        # heuristically determine if this is probably a VIP or anycast
        if iface.startswith("lo:anycast"):
            self.log_info(f"{address} on {iface} is being assigned as anycast.")
            role = "anycast"
        elif is_vdev:
            self.log_info(f"{address} on {iface} is being assigned as virtual.")
            role = "vip"
        else:
            role = ""

        # try to get the existing ip address object from netbox
        if is_vdev:
            try:
                # Try to find the address on the same machine but a different interface for reassignment
                if nbiface.virtual_machine is not None:
                    ipaddr = IPAddress.objects.get(address=str(address),
                                                   interface__virtual_machine=nbiface.virtual_machine)
                else:
                    ipaddr = IPAddress.objects.get(address=str(address),
                                                   interface__device=nbiface.device)
            except ObjectDoesNotExist:
                # else:
                # this is a vdev so create a new copy
                self.log_info(f"Creating a new copy of VIP {address}")
                ipaddr = IPAddress(address=str(address),
                                   interface=nbiface,
                                   role=role)
                ipaddr.save()
        else:
            try:
                ipaddr = IPAddress.objects.get(address=str(address))
            except ObjectDoesNotExist:
                self.log_info(f"Creating {address}")
                ipaddr = IPAddress(address=str(address),
                                   interface=nbiface, role=role)
                ipaddr.save()

        if ipaddr.interface:
            if ipaddr.interface.virtual_machine:
                olddev = ipaddr.interface.virtual_machine
            else:
                olddev = ipaddr.interface.device
        else:
            olddev = None

        if nbiface.virtual_machine:
            newdev = nbiface.virtual_machine
        else:
            newdev = nbiface.device

        if not ipaddr.interface:
            # no interface assigned
            self.log_info(f"Assigning {address} to {newdev}:{nbiface}")
        elif olddev != newdev:
            # the ip address is asigned to a completely different device
            # and this is not a vdev, reassign
            self.log_info(f"Taking IP address {ipaddr} from {olddev}:{ipaddr.interface}")
            self.log_info(f"Assigning {address} to {newdev}:{nbiface}")
        else:
            # on same device but different interface
            if ipaddr.interface.name not in networking["interfaces"]:
                # basically renaming a device so we need to copy the description field
                nbiface.description = ipaddr.interface.description
                nbiface.save()

        # finally actually reassign interface
        ipaddr.interface = nbiface

        if (iface == networking["primary"]):
            # Try assigning DNS name and getting information about DNS.
            output = self._assign_name(ipaddr, networking, is_ipv6)

            ipaddr.is_primary = True
            self.log_info(f"Setting {nbiface} as primary for {newdev}")
            if is_ipv6:
                newdev.primary_ip6 = ipaddr
            else:
                newdev.primary_ip4 = ipaddr
            newdev.save()

        ipaddr.role = role
        ipaddr.save()
        return output

    def _import_interfaces_for_device(self, device, cfg, is_virtual=False):
        """Import one device's interfaces from PuppetDB based on specified configuration."""
        output = []
        # Get networking facts
        puppetdb_url = "/".join([cfg["puppetdb"]["url"], "/v1/facts", "{}", device.name])
        response = requests.get(puppetdb_url.format("networking"), verify=cfg["puppetdb"]["ca_cert"])
        if response.status_code != 200:
            message = f"Cannot retrieve PuppetDB `networking` facts about {device.name}"
            self.log_failure(message)
            return message
        networking = response.json()
        # Get net_driver facts
        response = requests.get(puppetdb_url.format("net_driver"), verify=cfg["puppetdb"]["ca_cert"])
        if response.status_code != 200:
            message = f"Cannot retrieve PuppetDB `net_driver` facts about {device.name}"
            self.log_failure(message)
            return message
        net_driver = response.json()

        for iface, iface_dict in networking["interfaces"].items():
            is_vdev = ((":" in iface) or ("." in iface) or ("lo" == iface))
            try:
                nbiface = device.interfaces.get(name=iface)
            except ObjectDoesNotExist:
                self.log_info(f"Creating interface {iface} for device {device}".format(iface, device))

                iface_fmt = InterfaceTypeChoices.TYPE_1GE_FIXED

                # heuristically determine the device format
                if is_vdev:
                    iface_fmt = InterfaceTypeChoices.TYPE_VIRTUAL
                elif iface in net_driver:
                    if net_driver[iface]["speed"] == 10000:
                        iface_fmt = InterfaceTypeChoices.TYPE_10GE_SFP_PLUS

                # only set MTU if it is non-default
                mtu = None
                if "mtu" in iface_dict and iface_dict["mtu"] != 1500:
                    mtu = iface_dict["mtu"]

                if is_virtual:
                    nbiface = Interface(name=iface,
                                        mgmt_only=False,
                                        virtual_machine=device,
                                        type=iface_fmt,
                                        mtu=mtu)
                else:
                    nbiface = Interface(name=iface,
                                        mgmt_only=False,
                                        device=device,
                                        type=iface_fmt,
                                        mtu=mtu)
                nbiface.save()
            if "ip" in iface_dict:
                # process ipv4 address
                output = output + self._assign_ip_to_interface(nbiface, networking, iface, iface_dict, False, is_vdev)
            if "ip6" in iface_dict:
                # process ipv6 address
                output = output + self._assign_ip_to_interface(nbiface, networking, iface, iface_dict, True, is_vdev)

        return output + [f"{device.name} finished."]

    def run(self, data, commit):
        cfg = configparser.ConfigParser()
        cfg.read(CONFIGFILE)

        devices = Device.objects.filter(name__in=data['device'].split())
        vmdevices = VirtualMachine.objects.filter(name__in=data['device'].split())
        messages = []
        if not devices and not vmdevices:
            message = "No Netbox devices found for specified list."
            self.log_failure(message)
            return message

        for device in devices:
            self.log_info(f"Processing device {device}")
            messages = messages + self._import_interfaces_for_device(device, cfg)

        for device in vmdevices:
            self.log_info(f"Processing virtual device {device}")
            messages = messages + self._import_interfaces_for_device(device, cfg, True)

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
