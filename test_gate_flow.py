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
        self.assertIn('Potansiyel Gidişler', radar)
        self.assertIn('TK1821', radar)
        self.assertIn('A11', radar)
        self.assertIn('taksi', radar)
        self.assertIn('Kapanışa', radar)
        self.assertIn('Pay:', radar)

    def test_gate_closure_deadline_filtering(self):
        now = bot.get_now_ist()
        # 15 dk sonra kalkan uçağın kapısı 5 dk önce kapanmıştır (rem_gate_min = -5)
        closed_flight = {
            'flight_no': 'TK999',
            'dest': 'ROMA',
            'dep_time': now + timedelta(minutes=15),
            'gate': 'B6',
            'status': 'Son Çağrı'
        }
        radar = bot.render_gate_radar('B5', [], [closed_flight])
        # Kapı kapanış süresi geçtiği için potansiyel aktarma listesine girmemeli
        self.assertNotIn('TK999', radar)

    def test_departures_intent(self):
        intent1, a1, _ = bot.parse_user_intent('gidişler')
        self.assertEqual(intent1, 'DEPARTURES')
        intent2, a2, _ = bot.parse_user_intent('a gidiş')
        self.assertEqual(intent2, 'DEPARTURES_PIER')
        self.assertEqual(a2, 'A')

    def test_departures_radar_pagination_and_closure(self):
        now = bot.get_now_ist()
        # 15 uçuş üretelim
        mock_deps = []
        for i in range(15):
            mock_deps.append({
                'flight_no': f'TK10{i:02d}',
                'dest': f'CITY_{i}',
                'dest_iata': f'C{i}',
                'dep_time': now + timedelta(minutes=40 + i * 5),
                'gate': f'A{i+1}',
                'source_gate': f'A{i+1}',
                'status': 'Boarding'
            })
        sent_messages = []
        def fake_send(text, reply_markup=None, target_chat_id=None):
            sent_messages.append({'text': text, 'markup': reply_markup})
            return 999

        orig_fetch = bot.fetch_iga_direct_flights
        orig_send = bot.send_telegram
        try:
            bot.fetch_iga_direct_flights = lambda: (now, [], mock_deps, 'TEST')
            bot.send_telegram = fake_send
            bot.CONFIG['user_gate'] = 'A1'

            bot.execute_departures_radar(pier='A', page=1)
            self.assertEqual(len(sent_messages), 1)
            msg = sent_messages[0]
            self.assertIn('Sayfa 1/2', msg['text'])
            self.assertIn('kalkıştan 20 dk önce', msg['text'])
            # İlk uçuş kalkış 40 dk sonra -> Kapanışa 20 dk
            self.assertIn('Kapanışa <b>20 dk</b>', msg['text'])
            # Sayfa 1 için Devamı butonu olmalı
            inline_kb = msg['markup']['inline_keyboard']
            self.assertTrue(any('DEP_P_2_A' in btn.get('callback_data', '') for row in inline_kb for btn in row))
            self.assertTrue(any('DEP_PIER_ALL' in btn.get('callback_data', '') for row in inline_kb for btn in row))
        finally:
            bot.fetch_iga_direct_flights = orig_fetch
            bot.send_telegram = orig_send

    def test_callback_query_routing(self):
        called_args = []
        orig_exec = bot.execute_departures_radar
        try:
            bot.execute_departures_radar = lambda pier=None, page=1, message_id=None, chat_id=None: called_args.append({
                'pier': pier, 'page': page, 'message_id': message_id, 'chat_id': chat_id
            })

            # DEP_P_2_B
            bot.handle_telegram_message({'text': 'DEP_P_2_B', 'message_id': 123, 'chat': {'id': 456}})
            self.assertEqual(len(called_args), 1)
            self.assertEqual(called_args[0], {'pier': 'B', 'page': 2, 'message_id': 123, 'chat_id': '456'})

            # DEP_PIER_A
            bot.handle_telegram_message({'text': 'DEP_PIER_A', 'message_id': 789, 'chat': {'id': 456}})
            self.assertEqual(len(called_args), 2)
            self.assertEqual(called_args[1], {'pier': 'A', 'page': 1, 'message_id': 789, 'chat_id': '456'})

            # DEP_PIER_ALL
            bot.handle_telegram_message({'text': 'DEP_PIER_ALL', 'message_id': 790, 'chat': {'id': 456}})
            self.assertEqual(len(called_args), 3)
            self.assertEqual(called_args[2], {'pier': None, 'page': 1, 'message_id': 790, 'chat_id': '456'})
        finally:
            bot.execute_departures_radar = orig_exec

if __name__ == '__main__':
    unittest.main()

