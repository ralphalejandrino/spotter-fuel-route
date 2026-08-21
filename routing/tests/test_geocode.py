"""Endpoint resolution tests.

The border cases exist because the first implementation of the "both within the USA"
check was a latitude/longitude bounding box, and a box cannot represent a border: it
accepted Tijuana, Mexico and let the API plan a 1,102-mile international route. These
tests pin the real behaviour so that regression cannot come back quietly.
"""

from django.test import SimpleTestCase

from routing.geocode import GeocodeError, _in_us, resolve


class UsContainmentTests(SimpleTestCase):
    """Point-in-polygon against the Census nation boundary, not a bounding box."""

    INSIDE = [
        ("San Diego, CA", 32.7157, -117.1611),
        ("El Paso, TX", 31.7619, -106.4850),
        ("Detroit, MI", 42.3314, -83.0458),
        ("Seattle, WA", 47.6062, -122.3321),
        ("Miami, FL", 25.7617, -80.1918),
        ("Anchorage, AK", 61.2181, -149.9003),
        ("Honolulu, HI", 21.3069, -157.8583),
        ("San Juan, PR", 18.4655, -66.1057),
        ("New York, NY", 40.7128, -74.0060),
    ]

    # Each of these is deliberately close to, or historically confused with, a US point.
    OUTSIDE = [
        ("Tijuana, Mexico", 32.5149, -117.0382),          # 15 mi from San Diego
        ("Ciudad Juarez, Mexico", 31.6904, -106.4245),    # across the river from El Paso
        ("Windsor, Ontario", 42.3149, -83.0364),          # 1.5 km from Detroit
        ("Vancouver, Canada", 49.2827, -123.1207),
        ("Montreal, Canada", 45.5019, -73.5674),
        ("Quebec City, Canada", 46.8139, -71.2080),
        ("Havana, Cuba", 23.1136, -82.3666),
        ("Mexico City, Mexico", 19.4326, -99.1332),
        ("open Atlantic", 30.0, -70.0),
        ("London, UK", 51.5074, -0.1278),
    ]

    def test_us_points_are_accepted(self):
        for label, lat, lon in self.INSIDE:
            with self.subTest(label):
                self.assertTrue(_in_us(lat, lon), f"{label} should be inside the USA")

    def test_non_us_points_are_rejected(self):
        for label, lat, lon in self.OUTSIDE:
            with self.subTest(label):
                self.assertFalse(_in_us(lat, lon), f"{label} should be outside the USA")

    def test_a_bounding_box_would_not_pass_these(self):
        """Negative control on the test itself.

        Tijuana and Detroit/Windsor both sit inside any lat/lon rectangle that contains
        the continental US, so if this suite could be satisfied by a box it would not be
        testing anything. Assert that at least one rejected point lies within the US
        bounding envelope -- i.e. only a real polygon test can pass.
        """
        lats = [p[1] for p in self.INSIDE]
        lons = [p[2] for p in self.INSIDE]
        box = (min(lats), max(lats), min(lons), max(lons))
        inside_box_but_foreign = [
            label
            for label, lat, lon in self.OUTSIDE
            if box[0] <= lat <= box[1] and box[2] <= lon <= box[3]
        ]
        self.assertTrue(
            inside_box_but_foreign,
            "no foreign point falls inside the US envelope; these tests are too weak",
        )


class ResolveTests(SimpleTestCase):
    def test_city_and_state(self):
        lat, lon = resolve("Denver, CO")
        self.assertAlmostEqual(lat, 39.76, delta=0.4)
        self.assertAlmostEqual(lon, -104.88, delta=0.4)

    def test_full_state_name_works_too(self):
        self.assertEqual(resolve("Denver, Colorado"), resolve("Denver, CO"))

    def test_case_and_spacing_are_forgiving(self):
        self.assertEqual(resolve("  denver ,   co  "), resolve("Denver, CO"))

    def test_raw_coordinates_pass_through(self):
        self.assertEqual(resolve("39.7392,-104.9903"), (39.7392, -104.9903))

    def test_coordinates_outside_the_us_are_rejected(self):
        with self.assertRaises(GeocodeError) as ctx:
            resolve("32.5149,-117.0382")  # Tijuana
        self.assertIn("outside the United States", str(ctx.exception))

    def test_ambiguous_bare_city_names_the_states(self):
        with self.assertRaises(GeocodeError) as ctx:
            resolve("Springfield")
        msg = str(ctx.exception)
        self.assertIn("ambiguous", msg)
        self.assertIn("IL", msg)

    def test_unknown_place_suggests_the_format(self):
        with self.assertRaises(GeocodeError) as ctx:
            resolve("Nowheresville, ZZ")
        self.assertIn("lat,lon", str(ctx.exception))

    def test_empty_input_is_rejected(self):
        with self.assertRaises(GeocodeError):
            resolve("   ")

    def test_saint_and_st_are_interchangeable(self):
        self.assertEqual(resolve("St Louis, MO"), resolve("Saint Louis, MO"))

    def test_resolution_makes_no_network_call(self):
        """Endpoint geocoding must never add to the external API call budget."""
        from unittest import mock

        with mock.patch("socket.socket", side_effect=AssertionError("network used!")):
            resolve("Chicago, IL")
            resolve("41.8781,-87.6298")
