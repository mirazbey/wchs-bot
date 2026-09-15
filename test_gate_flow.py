import unittest
from datetime import datetime, timedelta, timezone
from gate_support import exact_gate, gate_targets, normalize_flight, GateBridgeClient
import bot

class TestGateFlow(unittest.TestCase):
    def setUp(self):
        bot.custom_flight_gates.clear()
        bot.custom_gate_times.clear()

    def test_gate_normalization_and_targets(self):
        self.assertEqual(exact_gate('a11'), 'A11')
        self.assertEqual(exact_gate('b5a'), 'B5A')
        self.assertEqual(exact_gate('f3'), 'F3')
        self.assertIsNone(exact_gate('xyz'))
        self.assertEqual(gate_targets('a11'), ['A11', 'A11A', 'A11B', 'A11L', 'A11R'])
        self.assertEqual(gate_targets('B5A'), ['B5A', 'B5R'])

    def test_base_gate_prominent_rendering(self):
        now = bot.get_now_ist()
        arrs = []
        deps = [{
            'flight_no': 'TK203',
            'dest': 'SEATTLE',
            'dest_iata': 'SEA',
            'dep_time': now + timedelta(minutes=24),
            'gate': 'A11',
            'source_gate': 'A11',
            'status': 'Son Çağrı'
        }]
        radar = bot.render_gate_radar('A11', arrs, deps)
        self.assertIn('A11', radar)
        self.assertIn('TK203', radar)
        self.assertIn('SEATTLE', radar)
        self.assertNotIn('A11A', radar)
        self.assertNotIn('A/B ayrımı kaynakta belirtilmemiş', radar)

    def test_subgate_rendering(self):
        now = bot.get_now_ist()
        arrs = [{
            'flight_no': 'TK0630',
            'origin_name': 'TRIPOLI',
            'origin_iata': 'TIP',
            'arr_time': now - timedelta(minutes=10),
            'gate': 'B5A',
            'source_gate': None,
            'is_oss': False,
            'status': 'İndi'
        }]
        deps = [{
            'flight_no': 'TK1821',
            'dest': 'PARIS',
            'dest_iata': 'CDG',
            'dep_time': now + timedelta(minutes=40),
            'gate': 'B5B',
            'source_gate': 'B5B',
            'status': 'Boarding'
        }]
        radar = bot.render_gate_radar('B5', arrs, deps)
        self.assertIn('B5A', radar)
        self.assertIn('TK0630', radar)
        self.assertIn('B5B', radar)
        self.assertIn('TK1821', radar)

    def test_empty_gate_rendering(self):
        radar = bot.render_gate_radar('F3', [], [])
        self.assertIn('Doğrulanmış uçuş bilgisi yok', radar)
        self.assertEqual(radar.count('Doğrulanmış uçuş bilgisi yok'), 2)

    def test_cache_ttl(self):
        bot.set_manual_gate('TK100', 'B5')
        self.assertEqual(bot.manual_gate('TK100'), 'B5')

    def test_crawler_window_filter(self):
        now = bot.get_now_ist()
        test_flights = [
            {'flight_no': 'TK1', 'arr_time': now - timedelta(minutes=20), 'gate': 'Kapı doğrulanamadı'},
            {'flight_no': 'TK2', 'arr_time': now + timedelta(minutes=10), 'gate': 'Kapı doğrulanamadı'},
            {'flight_no': 'TK3', 'arr_time': now - timedelta(minutes=40), 'gate': 'Kapı doğrulanamadı'},
            {'flight_no': 'TK4', 'arr_time': now + timedelta(minutes=35), 'gate': 'Kapı doğrulanamadı'},
        ]
        in_window = [
            f['flight_no'] for f in test_flights
            if -25 <= (f['arr_time'] - now).total_seconds() / 60 <= 20
        ]
        self.assertEqual(in_window, ['TK1', 'TK2'])

    def test_gate_potential_departures(self):
        now = bot.get_now_ist()
        arrs = [{
            'flight_no': 'TK1898',
            'origin_name': 'VİYANA',
            'origin_iata': 'VIE',
            'arr_time': now - timedelta(minutes=15),
            'gate': 'B5',
            'source_gate': 'B5',
            'is_oss': True,
            'status': 'İndi'
        }]
        deps = [{
            'flight_no': 'TK1821',
            'dest': 'PARİS',
            'dest_iata': 'CDG',
            'dep_time': now + timedelta(minutes=45),
            'gate': 'A11',
            'source_gate': 'A11',
            'status': 'Boarding'
        }]
        radar = bot.render_gate_radar('B5', arrs, deps)
        self.assertIn('Potansiyel Gidişler (İlk 2 Saat)', radar)
        self.assertIn('TK1821', radar)
        self.assertIn('A11', radar)
        self.assertIn('taksi', radar)

    def test_departures_intent(self):
        intent1, a1, _ = bot.parse_user_intent('gidişler')
        self.assertEqual(intent1, 'DEPARTURES')
        intent2, a2, _ = bot.parse_user_intent('a gidiş')
        self.assertEqual(intent2, 'DEPARTURES_PIER')
        self.assertEqual(a2, 'A')

if __name__ == '__main__':
    unittest.main()
