from extras.validators import CustomValidator


class Main(CustomValidator):
    def validate(self, instance):
        # Slug
        if len(instance.slug) != 5:
            self.fail("Invalid slug (must be 5 chars)")
        if instance.slug != instance.slug.lower():
            self.fail("Invalid slug (must be lowercase)")
