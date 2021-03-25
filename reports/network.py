"""
Report on various network related errors.

"""

from collections import defaultdict

from dcim.choices import DeviceStatusChoices
from dcim.constants import VIRTUAL_IFACE_TYPES
from ipam.choices import IPAddressRoleChoices


from dcim.models import Device, Interface
from extras.reports import Report
from ipam.models import IPAddress

EXCLUDE_STATUSES = (
    DeviceStatusChoices.STATUS_DECOMMISSIONING,
    DeviceStatusChoices.STATUS_INVENTORY,
    DeviceStatusChoices.STATUS_OFFLINE,
    DeviceStatusChoices.STATUS_PLANNED,
)
SWITCHES_ROLES = ("asw", "msw", "cloudsw")
NETWORK_ROLES = ("asw", "cr", "mr", "pfw", "cloudsw")


ACCESS_INTERFACES_PREFIX = ("xe-", "ge-")


class Network(Report):
    """Report on various network-related errors."""

    description = __doc__

    def test_duplicate_interface(self):
        """Report duplicated interfaces on switches (and switch stack).
        
        For example if xe-2/0/18 and ge-2/0/18 are present. Or if xe-2/0/18 exists twice on 2 different VC members.
        """
        seen_interfaces = defaultdict(set)
        for interface in Interface.objects.filter(device__device_role__slug__in=SWITCHES_ROLES, ).exclude(device__status__in=EXCLUDE_STATUSES):
            if not str(interface.name).startswith(ACCESS_INTERFACES_PREFIX):
                # TODO convert to Django filter?
                continue
            if interface.device.vc_position:
                interface_fpc = "-{}/".format(interface.device.vc_position)
                if interface_fpc not in interface.name:
                    self.log_failure(interface, "Interface doesn't match its switch member: {}.".format(interface.device.vc_position))
                    continue
            else:
                if "-0/" not in interface.name:
                    self.log_failure(interface, "Interface on a non-VC should start with -0/.")
                    continue
            
            if interface.name.split('-')[1] in seen_interfaces[interface.device.name]:
                self.log_failure(interface, "Duplicated interface with different prefix (eg. xe- & ge-).")
                continue
            else:
                seen_interfaces[interface.device.name].add(interface.name.split('-')[1])

    def test_enabled_not_connected(self):
        """No interface on a network device should be enabled but not connected."""

        for interface in (Interface.objects.filter(device__device_role__slug__in=NETWORK_ROLES)
                                           .exclude(device__status__in=EXCLUDE_STATUSES)
                                           .exclude(cable__isnull=False)
                                           .exclude(type__in=VIRTUAL_IFACE_TYPES)
                                           .exclude(mgmt_only=True)
                                           .exclude(enabled=False)):
            self.log_failure(interface, "Interface enabled but not connected.")

    def test_primary_ipv6(self):
        """Report servers that either have a missing primary_ipv6 or have a primary_ipv6 without a DNS name set.
        
        To help with T253173.
        """

        success=0
        for device in (Device.objects.filter(device_role__slug="server")
                                            .exclude(tenant__slug="fr-tech")
                                            .exclude(status__in=EXCLUDE_STATUSES)):
            if not device.primary_ip6:
                self.log_warning(device, "Missing primary IPv6")
                continue
            else:
                if not device.primary_ip6.dns_name:
                    self.log_warning(device, "Primary IPv6 missing DNS name.")
                    continue
            success+=1
        self.log_success(None, "{} devices with operationnal primary IPv6".format(success))

    def test_duplicate_ip_netmask(self):
        """Report to check for duplicate IPs with different netmasks.

        When an IP is marked as VIP for example, Netbox allows to create it duplicated with different netmasks like 10.0.0.0/32 and 10.0.0.0/27.
        We should alert if this happens as it's an indication that something has not worked properly.
        We set a netmask of /32 (correct one) for all VIPs and not the subnet netmask as Netbox does by default.
        See also T273248#6791839
        """

        success=0
        seen_ipaddress = defaultdict(int)
        for ipaddress in IPAddress.objects.filter(role=IPAddressRoleChoices.ROLE_VIP):
            if seen_ipaddress[ipaddress.address.ip] and seen_ipaddress[ipaddress.address.ip] != ipaddress.address.prefixlen:
                self.log_failure(ipaddress, "Multiple VIPs with different prefix length.")
                continue
            else:
                seen_ipaddress[ipaddress.address.ip] = ipaddress.address.prefixlen
            success+=1

        self.log_success(None, "{} fine VIPs".format(success))
