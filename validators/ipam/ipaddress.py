from extras.validators import CustomValidator

from validators import domain


class Main(CustomValidator):
    def validate(self, instance):
        # dns_name
        if instance.dns_name.endswith("."):
            self.fail("Invalid DNS name (must not end with a dot)")
        if not domain(instance.dns_name):
            self.fail("Invalid DNS name (must be a FQDN)")
