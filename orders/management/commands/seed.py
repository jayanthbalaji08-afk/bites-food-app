from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from orders.models import Restaurant, MenuItem, Profile

DATA = [
    ("Meghana Foods", "Andhra, Biryani", "Koramangala, Bengaluru", 12.9352, 77.6245, [
        ("Chicken Biryani", "Spicy Andhra-style", 320, False),
        ("Paneer Biryani", "Rich and aromatic", 280, True),
        ("Chilli Chicken", "Dry, spicy starter", 260, False)]),
    ("Udupi Tiffin", "South Indian", "Indiranagar, Bengaluru", 12.9784, 77.6408, [
        ("Masala Dosa", "Crisp with potato filling", 90, True),
        ("Idli Vada", "Soft idli with crispy vada", 70, True),
        ("Filter Coffee", "Strong and hot", 40, True)]),
    ("Pizza Corner", "Italian", "HSR Layout, Bengaluru", 12.9116, 77.6474, [
        ("Margherita Pizza", "Classic cheese and tomato", 250, True),
        ("Pepperoni Pizza", "Spicy pepperoni", 350, False),
        ("Garlic Bread", "Buttery and toasted", 120, True)]),
    ("Noodle House", "Chinese", "Jayanagar, Bengaluru", 12.9250, 77.5938, [
        ("Veg Hakka Noodles", "Wok-tossed", 180, True),
        ("Chicken Fried Rice", "With egg and spring onion", 220, False),
        ("Manchurian Gravy", "Veg balls in sauce", 190, True)]),
]

DEMO_OWNER = ("owner", "owner12345")


class Command(BaseCommand):
    help = "Add sample restaurants and a demo restaurant-owner account"

    def handle(self, *args, **opts):
        if Restaurant.objects.exists():
            self.stdout.write("Restaurants already exist.")
        else:
            for name, cuisine, addr, lat, lng, items in DATA:
                r = Restaurant.objects.create(name=name, cuisine=cuisine, address=addr, latitude=lat, longitude=lng)
                for n, d, p, v in items:
                    MenuItem.objects.create(restaurant=r, name=n, description=d, price=p, is_veg=v)
            self.stdout.write(self.style.SUCCESS("Sample restaurants added."))

        username, password = DEMO_OWNER
        user, created = User.objects.get_or_create(username=username)
        if created:
            user.set_password(password)
            user.save()
        Profile.objects.update_or_create(user=user, defaults={"is_owner": True})
        first = Restaurant.objects.filter(owner__isnull=True).order_by("pk").first()
        if first and not Restaurant.objects.filter(owner=user).exists():
            first.owner = user
            first.save()
        if created:
            self.stdout.write(self.style.SUCCESS(
                f"Demo owner created -> username: {username}  password: {password}  (owns {first})"))
            self.stdout.write("This demo account is for local testing only. Delete it before going live.")
