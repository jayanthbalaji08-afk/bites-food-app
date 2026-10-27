import math
import re

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from .models import Profile, Restaurant, MenuItem

MAX_IMAGE_MB = 3


def clean_phone_value(value):
    phone = re.sub(r"[\s-]", "", value or "")
    if not re.fullmatch(r"\+?\d{10,13}", phone):
        raise forms.ValidationError("Enter a valid phone number (10 to 13 digits).")
    return phone


def check_image(img):
    # Only a fresh upload has content_type; skip existing files and "clear" requests.
    if img and hasattr(img, "content_type") and img.size > MAX_IMAGE_MB * 1024 * 1024:
        raise forms.ValidationError(f"Image must be smaller than {MAX_IMAGE_MB} MB.")
    return img


class SignupForm(UserCreationForm):
    email = forms.EmailField(required=False)
    phone = forms.CharField(max_length=20, required=False)
    is_owner = forms.BooleanField(required=False, label="I run a restaurant and want to sell on Bites")

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")

    def clean_phone(self):
        value = self.cleaned_data.get("phone")
        return clean_phone_value(value) if value else ""

    def save(self, commit=True):
        user = super().save(commit=True)
        Profile.objects.update_or_create(user=user, defaults={
            "phone": self.cleaned_data.get("phone", ""),
            "is_owner": self.cleaned_data.get("is_owner", False),
        })
        return user


class CheckoutForm(forms.Form):
    name = forms.CharField(max_length=100, label="Name")
    phone = forms.CharField(max_length=20, label="Phone")
    address = forms.CharField(max_length=250, label="Delivery address")
    payment_method = forms.ChoiceField(choices=[("cod", "Cash on delivery"), ("online", "Pay online")])
    lat = forms.FloatField(required=False, widget=forms.HiddenInput)
    lng = forms.FloatField(required=False, widget=forms.HiddenInput)

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError("Enter your name.")
        return name

    def clean_address(self):
        address = self.cleaned_data["address"].strip()
        if len(address) < 5:
            raise forms.ValidationError("Enter a complete delivery address.")
        return address

    def clean_phone(self):
        return clean_phone_value(self.cleaned_data["phone"])

    def clean(self):
        data = super().clean()
        lat, lng = data.get("lat"), data.get("lng")
        if lat is not None and lng is not None:
            if not (math.isfinite(lat) and math.isfinite(lng) and -90 <= lat <= 90 and -180 <= lng <= 180):
                data["lat"] = data["lng"] = None
        else:
            data["lat"] = data["lng"] = None
        return data


class RestaurantForm(forms.ModelForm):
    class Meta:
        model = Restaurant
        fields = ["name", "cuisine", "address", "latitude", "longitude", "image", "is_open"]
        labels = {"is_open": "Open for orders"}
        help_texts = {"latitude": "Use the location button below, or copy from Google Maps.",
                      "longitude": ""}

    def clean_latitude(self):
        v = self.cleaned_data["latitude"]
        if not (math.isfinite(v) and -90 <= v <= 90):
            raise forms.ValidationError("Latitude must be between -90 and 90.")
        return v

    def clean_longitude(self):
        v = self.cleaned_data["longitude"]
        if not (math.isfinite(v) and -180 <= v <= 180):
            raise forms.ValidationError("Longitude must be between -180 and 180.")
        return v

    def clean_image(self):
        return check_image(self.cleaned_data.get("image"))


class MenuItemForm(forms.ModelForm):
    class Meta:
        model = MenuItem
        fields = ["name", "description", "price", "is_veg", "available", "image"]
        labels = {"is_veg": "Vegetarian", "available": "Available now"}

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError("Enter a dish name.")
        return name

    def clean_price(self):
        price = self.cleaned_data["price"]
        if price <= 0:
            raise forms.ValidationError("Price must be more than zero.")
        return price

    def clean_image(self):
        return check_image(self.cleaned_data.get("image"))
