from extras.validators import CustomValidator

from validators import domain


class main(CustomValidator):

    def validate(self, instance):
        # tags
        if instance.tags:
            self.fail("Tags not allowed")

        # dns_name
        if instance.dns_name.endswith('.'):
            self.fail("Invalid DNS name (must not end with a dot)")
        if not domain(instance.dns_name):
            self.fail("Invalid DNS name (must be a FQDN)")
