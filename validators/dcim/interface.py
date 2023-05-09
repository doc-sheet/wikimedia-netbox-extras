"""Validator class for the Interface model."""

from extras.validators import CustomValidator

NETWORK_ROLES = ("asw", "cr", "mr", "pfw", "cloudsw")

# For ergonomics the regexps that match interface names are placed in this tuple.
INTERFACES_REGEXP = re.compile(
    (
        r"|".join(
            (
                r"^mgmt\d?$|^ILO$|^i?DRAC$",  # managment interfaces
                r"^fxp\d-re\d$",  # routing engine management interfaces
                r"^[a-z]+-\d+/\d+/\d+(:\d+){0,1}(\.\d+){0,1}$",  # Juniper interfaces eg et-0/0/0:0.0
                r"^[a-z]{1,4}(\d+){0,1}(\.\d+){0,1}$",  # typical device names (eg eth0) and vlan.900 etc.
                r"^en(p\d+)?s\d+(f\d+)?((d|np)\d+)?$",  # systemd 'path' devices
                r"^cali[a-f0-9]+",  # kubernetes calico interfaces
                r"^(tap|brq)[a-f0-9]+\-[a-f0-9]+",  # cloudnet interfaces
                r"^vxlan-\d+",  # cloudnet vxlan interfaces
                r"^(br-internal|br-external|br-int)$",  # more cloudnet interfaces
                r"^\d+$",  # Netgear switch interfaces are just numbers.
                r"^lo([.:].+)",  # virtual interfaces
                r"^(public|private)$",  # ganeti interfaces
                r"^##PRIMARY##$",  # interface name placeholder
            )
        )
    )
)


class Main(CustomValidator):
    """Main class referenced in the Netbox config"""

    def validate(self, instance):
        """Mandatory entry point"""
        # Name
        if not INTERFACES_REGEXP.fullmatch(instance.name):
            self.fail("Invalid name (must match the INTERFACES_REGEXP options)")

        # MTU
        if (
            hasattr(instance.connected_endpoint, "device")
            and instance.device.device_role.slug in NETWORK_ROLES  # Network devices
            and instance.mtu != 9192  # Ignore good MTU
            and not instance.lag  # Ignore LAG members
            and not (
                instance.device.tenant and instance.device.tenant.slug == "fr-tech"
            )  # Ignore frack devices
            and instance.enabled  # Ignore disabled interfaces
            and not str(instance.name).startswith("vcp-")
        ):  # Ignore VC links
            self.fail("Invalid MTU (must be 9192)")

        # Attributes
        attributes = [
            "description",
            "lag",
            "mtu",
            "mode",
            "mac_address",
            "count_ipaddresses",
        ]
        if (
            "no-mon"
            not in str(instance.description)  # doesn't have "no-mon" in description
            and not instance.enabled  # disabled interface
            and instance.device.device_role.slug in NETWORK_ROLES
        ):  # network devices only
            for attribute in attributes:
                if getattr(instance, attribute):
                    self.fail(
                        f"Invalid {attribute} (must not be set on disabled interfaces)"
                    )
