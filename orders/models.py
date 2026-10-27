import math

from django.conf import settings
from django.db import models
from django.templatetags.static import static
from django.utils import timezone

from .geo import distance_km, interpolate

# If a dish has no uploaded photo, an illustration from orders/static/orders/img/
# is picked from words in the dish name.
ICON_RULES = [
    (("biryani",), "biryani"), (("dosa",), "dosa"), (("idli", "vada"), "idli"),
    (("pepperoni",), "pepperoni"), (("pizza",), "pizza"),
    (("noodle",), "noodles"), (("rice",), "rice"), (("bread",), "bread"),
    (("coffee", "tea", "chai"), "coffee"), (("burger",), "burger"),
    (("chilli", "manchurian", "paneer", "chicken", "tikka", "kebab", "fry"), "starter"),
]


def icon_for(text):
    t = text.lower()
    for words, icon in ICON_RULES:
        if any(w in t for w in words):
            return icon
    return "dish"


class Profile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, related_name="profile", on_delete=models.CASCADE)
    phone = models.CharField(max_length=20, blank=True)
    default_address = models.CharField(max_length=250, blank=True)
    is_owner = models.BooleanField(default=False, help_text="Can manage a restaurant")

    def __str__(self):
        return f"Profile of {self.user}"


class Restaurant(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              related_name="restaurants", on_delete=models.SET_NULL)
    name = models.CharField(max_length=100)
    cuisine = models.CharField(max_length=100, blank=True)
    address = models.CharField(max_length=200, blank=True)
    latitude = models.FloatField()
    longitude = models.FloatField()
    image = models.ImageField(upload_to="restaurants/", blank=True)
    is_open = models.BooleanField(default=True)

    @property
    def picture_url(self):
        return self.image.url if self.image else static("orders/img/restaurant.svg")

    def __str__(self):
        return self.name


class MenuItem(models.Model):
    restaurant = models.ForeignKey(Restaurant, related_name="items", on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=200, blank=True)
    price = models.DecimalField(max_digits=7, decimal_places=2)
    is_veg = models.BooleanField(default=True)
    available = models.BooleanField(default=True)
    image = models.ImageField(upload_to="dishes/", blank=True)

    @property
    def picture_url(self):
        if self.image:
            return self.image.url
        return static(f"orders/img/{icon_for(self.name)}.svg")

    def __str__(self):
        return f"{self.name} ({self.restaurant.name})"


class Order(models.Model):
    STATUS = [("pending_payment", "Awaiting payment"), ("placed", "Placed"),
              ("preparing", "Preparing"), ("on_the_way", "On the way"),
              ("delivered", "Delivered"), ("cancelled", "Cancelled")]
    PAYMENT_METHODS = [("cod", "Cash on delivery"), ("online", "Online payment")]
    PAYMENT_STATUS = [("pending", "Pending"), ("paid", "Paid"),
                      ("failed", "Failed"), ("refunded", "Refunded")]
    NEXT = {"placed": "preparing", "preparing": "on_the_way", "on_the_way": "delivered"}

    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             related_name="orders", on_delete=models.SET_NULL)
    restaurant = models.ForeignKey(Restaurant, related_name="orders", on_delete=models.PROTECT)
    customer_name = models.CharField(max_length=100)
    phone = models.CharField(max_length=20)
    address = models.CharField(max_length=250)
    total = models.DecimalField(max_digits=9, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS, default="placed")
    payment_method = models.CharField(max_length=10, choices=PAYMENT_METHODS, default="cod")
    payment_status = models.CharField(max_length=10, choices=PAYMENT_STATUS, default="pending")
    payment_ref = models.CharField(max_length=40, blank=True)
    dest_lat = models.FloatField(null=True, blank=True)
    dest_lng = models.FloatField(null=True, blank=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Order #{self.pk} - {self.customer_name}"

    # ----- status changes -----
    def advance(self):
        new = self.NEXT.get(self.status)
        if not new:
            return False
        self.status = new
        if new == "on_the_way":
            self.dispatched_at = timezone.now()
        if new == "delivered" and self.payment_method == "cod":
            self.payment_status = "paid"
        self.save()
        return True

    @property
    def can_cancel_by_customer(self):
        return self.status in ("pending_payment", "placed")

    @property
    def can_cancel_by_owner(self):
        return self.status in ("placed", "preparing")

    def cancel(self):
        self.status = "cancelled"
        if self.payment_status == "paid":
            self.payment_status = "refunded"
        self.save()

    # ----- live tracking -----
    @property
    def destination(self):
        if self.dest_lat is not None and self.dest_lng is not None:
            return (self.dest_lat, self.dest_lng)
        # no location shared: place the customer about 1 km away so the map still works
        return (self.restaurant.latitude + 0.009, self.restaurant.longitude + 0.009)

    @property
    def distance_km(self):
        d = self.destination
        return distance_km(self.restaurant.latitude, self.restaurant.longitude, d[0], d[1])

    @property
    def delivery_seconds(self):
        # Demo speed: DEMO_SECONDS_PER_KM real seconds per km (so the rider visibly moves).
        return max(45, int(self.distance_km * settings.DEMO_SECONDS_PER_KM))

    def progress(self):
        if self.status == "delivered":
            return 1.0
        if self.status == "on_the_way" and self.dispatched_at:
            elapsed = (timezone.now() - self.dispatched_at).total_seconds()
            return max(0.0, min(1.0, elapsed / self.delivery_seconds))
        return 0.0

    def rider_position(self):
        d = self.destination
        return interpolate(self.restaurant.latitude, self.restaurant.longitude, d[0], d[1], self.progress())

    def eta_minutes(self):
        if self.status == "on_the_way":
            remaining = self.delivery_seconds * (1 - self.progress())
            return max(0, math.ceil(remaining / 60))
        return 15 + round(self.distance_km * 4)


class OrderItem(models.Model):
    order = models.ForeignKey(Order, related_name="lines", on_delete=models.CASCADE)
    item = models.ForeignKey(MenuItem, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    price = models.DecimalField(max_digits=7, decimal_places=2)
