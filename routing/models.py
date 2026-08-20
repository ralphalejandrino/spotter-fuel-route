from django.db import models


class FuelStation(models.Model):
    """One truck stop, at one location, with one representative price.

    The source CSV is not one-row-per-station: 678 OPIS IDs appear more than once, and
    597 of those repeats disagree on price while agreeing on everything else. There is
    no date or grade column to tell them apart, so they are repeated observations of the
    same pump and are aggregated at load time (see `load_fuel_prices`).
    """

    opis_id = models.IntegerField(unique=True, db_index=True)
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=300)
    city = models.CharField(max_length=120)
    state = models.CharField(max_length=2, db_index=True)
    retail_price = models.FloatField(help_text="USD per gallon")
    lat = models.FloatField()
    lon = models.FloatField()
    price_observations = models.IntegerField(default=1)
    geocode_source = models.CharField(max_length=16, default="gazetteer")

    class Meta:
        indexes = [models.Index(fields=["lat", "lon"])]

    def __str__(self) -> str:
        return f"{self.name} ({self.city}, {self.state}) ${self.retail_price:.3f}"
