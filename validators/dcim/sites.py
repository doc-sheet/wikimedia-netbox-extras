from extras.validators import CustomValidator

class main(CustomValidator):

    def validate(self, instance):
        # Tags
        if instance.tags:
            self.fail("Tags not allowed")

        # Slug
        if len(instance.slug) != 5:
            self.fail("Invalid slug (must be 5 chars)")
        if instance.slug != instance.slug.lower():
            self.fail("Invalid slug (must be lowercase)")
