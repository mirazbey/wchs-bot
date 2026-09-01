import unittest
from iga_client import IGAClient, FlightInfo

class TestIGAClient(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = IGAClient()

    def test_fetch_departures(self):
        flights = self.client.fetch_flights(nature=1, page_size=10)
        self.assertIsInstance(flights, list)
        self.assertGreater(len(flights), 0)
        flight = flights[0]
        self.assertIsInstance(flight, FlightInfo)
        self.assertTrue(len(flight.flight_number) > 0)
        self.assertEqual(flight.nature, "DEPARTURE")

    def test_fetch_arrivals(self):
        flights = self.client.fetch_flights(nature=0, page_size=10)
        self.assertIsInstance(flights, list)
        self.assertGreater(len(flights), 0)
        self.assertEqual(flights[0].nature, "ARRIVAL")

    def test_search_flight(self):
        active = self.client.fetch_flights(nature=1, page_size=5)
        self.assertGreater(len(active), 0)
        test_flight = active[0]
        
        found = self.client.get_flight_gate(test_flight.flight_number)
        self.assertIsNotNone(found)
        self.assertEqual(found.flight_number, test_flight.flight_number)

if __name__ == "__main__":
    unittest.main()
