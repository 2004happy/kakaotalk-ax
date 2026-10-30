import base64
import json
import os
import plistlib
import sqlite3
import stat
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import kakaotalk_reader as db
from kakaotalk_service import Reader, card_content, date_bound, media_entries, time_window, visible_media
from kakaotalk_media import CDNRedirects, retrieve, validate_url
from kakaotalk_ui import snapshot


def ts(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=db.KST).timestamp())


class FixtureReader(Reader):
    """Exercise the production SQL against an isolated SQLite schema."""
    def __init__(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.user_id, self._rooms, self._users, self._columns = 42, None, None, {}
        self.connection.executescript('''
          CREATE TABLE NTChatRoom(chatId INTEGER,linkId INTEGER,type INTEGER,chatName TEXT,lastLogId INTEGER,
            directChatMemberUserId INTEGER,activeMembersCount INTEGER,countOfNewMessage INTEGER,
            displayMemberIds BLOB,watermarkKeys BLOB);
          CREATE TABLE NTUser(userId INTEGER,linkId INTEGER,nickName TEXT,friendNickName TEXT,displayName TEXT);
          CREATE TABLE NTChatMeta(chatId INTEGER,type INTEGER,content TEXT,groupNickname TEXT,noticeText TEXT,updatedAt INTEGER);
          CREATE TABLE NTOpenLink(linkId INTEGER,linkName TEXT);
          CREATE TABLE NTChatMessage(chatId INTEGER,logId INTEGER,msgId INTEGER,authorId INTEGER,type INTEGER,
            sentAt INTEGER,message TEXT,attachment TEXT,supplement TEXT,localFilePath TEXT);
          CREATE TABLE NTChatFolder(folderId TEXT,name TEXT,type INTEGER,sortOrder INTEGER,hidden INTEGER,chatIds BLOB);
          CREATE TABLE NTBookmark(chatId INTEGER,logId INTEGER,type INTEGER,displayName TEXT,createAt INTEGER);
          CREATE TABLE NTEvent(eventId TEXT,chatId INTEGER,subject TEXT,startAt INTEGER,endAt INTEGER,isAllDay INTEGER,
            note TEXT,memo TEXT,locationInfo TEXT,timeZone TEXT,recurrenceRule TEXT);
          INSERT INTO NTUser VALUES(42,0,'나','',''),(100,0,'Alice','알리',''),(101,0,'Bob','','');
          INSERT INTO NTOpenLink VALUES(500,'동네방');
          INSERT INTO NTChatMeta VALUES(1,3,'친구방','친구방','',0),(1,1,'공지 본문','','공지 본문',1),
            (6,3,'친구방 뒷방','친구방 뒷방','',0);
          INSERT INTO NTBookmark VALUES(1,10,1,'중요',1);
        ''')
        for rid, lid, typ, name, direct, members, unread in [
            (1, 0, 1, '공유 제목', 0, 3, 2), (2, 500, 4, '', 0, 2, 0),
            (3, 0, 0, '', 100, 2, 0), (4, 0, 5, '', 0, 1, 0),
            (5, 0, 1, '', 0, 3, 0), (6, 0, 1, '', 0, 3, 0),
        ]:
            self.connection.execute('INSERT INTO NTChatRoom VALUES(?,?,?,?,?,?,?,?,?,?)',
                (rid, lid, typ, name, 1000 - rid, direct, members, unread,
                 plistlib.dumps([100, 101]), plistlib.dumps([42, 100, 101]) if members == 3 else None))
        self.connection.execute('INSERT INTO NTChatFolder VALUES(?,?,?,?,?,?)',
                                ('f1', '작업', 0, 0, 0, plistlib.dumps([1, 2])))
        self.connection.execute('INSERT INTO NTEvent VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            ('e1', 1, '일정', ts('2026-10-30T10:00:00'), ts('2026-10-30T11:00:00'), 0, '', '', '{}', 'Asia/Seoul', ''))
        self.add(10, 'ordinary', '2026-10-29T12:00:00', author=42)
        self.add(11, "quota 100%_ and quote's", '2026-10-30T00:00:00')
        self.add(12, '미리보기', '2026-10-30T09:00:00', kind=72,
                 attachment={'C': {'TI': {'TD': {'T': '카드 제목', 'D': '긴 본문 ' + '가' * 450 + ' 숨은단어'}},
                                    'BUL': [{'BU': {'T': '확인'}, 'L': {'LMO': 'https://example.test/private'}}]}})
        self.add(13, '답글', '2026-10-30T10:00:00', kind=26,
                 attachment={'src_logId': 10, 'src_userId': 42, 'src_type': 1, 'src_message': '인용맥락'})
        self.add(14, '사진 2장', '2026-10-30T11:00:00', kind=27,
                 attachment={'imageUrls': ['https://talk.kakaocdn.net/one', 'https://talk.kakaocdn.net/two']})
        self.add(15, 'milliseconds', '2026-10-30T23:59:59', milliseconds=True)
        self.add(16, 'next day', '2026-10-31T00:00:00')
        self._tables = {r['name'] for r in self.query("SELECT name FROM sqlite_master WHERE type='table';")}

    def add(self, log_id, text, when, kind=1, attachment=None, author=100, milliseconds=False):
        stamp = ts(when) * (1000 if milliseconds else 1)
        self.connection.execute('INSERT INTO NTChatMessage VALUES(?,?,?,?,?,?,?,?,?,?)',
            (1, log_id, 0, author, kind, stamp, text, json.dumps(attachment or {}, ensure_ascii=False), '', ''))

    def query(self, sql):
        db.validate_read_sql(sql)
        return [dict(row) for row in self.connection.execute(sql).fetchall()]


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.reader = FixtureReader()
        self.addCleanup(self.reader.connection.close)

    def test_private_alias_open_title_contact_alias_and_self_name(self):
        names = {r['chatId']: r for r in self.reader.rooms()['items']}
        self.assertEqual(names[1]['displayName'], '친구방')
        self.assertEqual(names[1]['nameSource'], 'local_nickname')
        self.assertEqual(names[2]['displayName'], '동네방')
        self.assertEqual(names[2]['nameSource'], 'open_link')
        self.assertEqual(names[3]['displayName'], '알리')
        self.assertEqual(names[4]['displayName'], '나와의 채팅')
        self.assertFalse(names[5]['nameExact'])

    def test_full_membership_does_not_use_only_displayed_members(self):
        room = self.reader.room(1)
        self.assertEqual(len(room['members']), 3)
        self.assertTrue(room['membersComplete'])
        self.assertEqual({r['userId'] for r in room['members']}, {42, 100, 101})
        self.assertEqual(room['notices'][0]['text'], '공지 본문')

    def test_ambiguous_partial_name_never_selects_a_random_room(self):
        self.assertEqual(self.reader.find_room(room_name='친구방')['chatId'], 1)
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            self.reader.find_room(room_name='친구')
        with self.assertRaisesRegex(ValueError, 'different rooms'):
            self.reader.find_room(room_id=1, room_name='동네방')

    def test_all_rooms_can_be_paginated_beyond_fifty(self):
        for rid in range(10, 75):
            self.reader.connection.execute('INSERT INTO NTChatRoom(chatId,linkId,type,chatName,lastLogId,activeMembersCount) VALUES(?,0,1,?,?,2)',
                                           (rid, f'방 {rid}', 100 - rid))
        first = self.reader.rooms(limit=50)
        second = self.reader.rooms(limit=50, offset=first['nextOffset'])
        ids = [r['chatId'] for r in first['items'] + second['items']]
        self.assertEqual(len(ids), first['total'])
        self.assertEqual(len(ids), len(set(ids)))
        self.assertFalse(second['hasMore'])

    def test_message_pages_are_disjoint_and_ordered(self):
        first = self.reader.messages(room_id=1, limit=2)
        second = self.reader.messages(room_id=1, limit=2, offset=first['nextOffset'])
        first_ids = [r['logId'] for r in first['items']]
        self.assertEqual(first_ids, sorted(first_ids))
        self.assertFalse(set(first_ids) & {r['logId'] for r in second['items']})
        self.assertEqual(first['total'], 7)

    def test_day_filter_handles_both_seconds_and_milliseconds(self):
        result = self.reader.messages(since='2026-10-30', until='2026-10-30')
        self.assertEqual([r['logId'] for r in result['items']], [11, 12, 13, 14, 15])
        exact = self.reader.messages(since='2026-10-30T09:00:00+09:00', until='2026-10-30T11:00:00+09:00')
        self.assertEqual([r['logId'] for r in exact['items']], [12, 13])

    def test_full_card_and_reply_search_are_not_limited_to_previews(self):
        result = self.reader.messages(keyword='숨은단어')
        self.assertEqual(result['total'], 1)
        item = result['items'][0]
        self.assertIn('숨은단어', item['text'])
        self.assertEqual(item['messageKind'], 'notification_card')
        self.assertEqual(item['buttons'], ['확인'])
        self.assertNotIn('https://example.test/private', json.dumps(item))
        quoted = self.reader.messages(keyword='인용맥락')
        self.assertEqual(quoted['items'][0]['reply']['logId'], 10)

    def test_empty_search_and_wildcards_are_literal(self):
        self.assertEqual(self.reader.messages(keyword='not present')['items'], [])
        self.assertEqual(self.reader.messages(keyword='100%_')['total'], 1)
        self.assertEqual(self.reader.messages(keyword="quote's")['total'], 1)

    def test_unread_filters_and_media_metadata(self):
        self.assertEqual(self.reader.rooms(unread_only=True)['total'], 1)
        item = self.reader.message(1, 14)
        self.assertEqual(item['messageKind'], 'album')
        self.assertEqual(len(item['media']), 2)
        self.assertNotIn('https://', json.dumps(item['media']))

    def test_notices_folders_bookmarks_and_calendar(self):
        self.assertEqual(self.reader.notices(1)['total'], 1)
        self.assertEqual(self.reader.folders()['items'][0]['chatIds'], [1, 2])
        self.assertEqual(self.reader.bookmarks(1)['items'][0]['logId'], 10)
        result = self.reader.events('2026-10-30', '2026-10-30')
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['items'][0]['startTime'], '2026-10-30 10:00:00')

    def test_activity_and_coverage(self):
        result = self.reader.activity('2026-10-30', '2026-10-30')
        self.assertEqual(result['messageCount'], 5)
        self.assertEqual(result['items'][0]['roomName'], '친구방')
        self.assertFalse(self.reader.status()['sendingSupported'])
        self.assertEqual(self.reader.status()['newestTime'], '2026-10-31 00:00:00')

    def test_limits_and_unknown_rooms_are_errors(self):
        for limit in (0, -1, 1001, True):
            with self.assertRaises(ValueError):
                self.reader.rooms(limit=limit)
        with self.assertRaises(ValueError):
            self.reader.room(999)
        with self.assertRaises(ValueError):
            self.reader.messages(since='not a date')


class TimeAndSQLTests(unittest.TestCase):
    def test_rolling_week_and_calendar_end_are_different(self):
        now = datetime(2026, 10, 30, 15, 30, tzinfo=db.KST)
        self.assertEqual(date_bound('last7d', now=now), now - timedelta(days=7))
        self.assertEqual(date_bound('-7d', now=now).hour, 0)
        self.assertEqual(date_bound('2026-10-30', end=True), datetime(2026, 10, 31, tzinfo=db.KST))
        with self.assertRaises(ValueError):
            time_window('2026-10-31', '2026-10-30T12:00:00+09:00')

    def test_sql_shell_and_filesystem_escape_are_rejected(self):
        for query in ['.shell whoami', "SELECT 1;\n.shell whoami", "SELECT readfile('/tmp/private')", "SELECT writefile('/tmp/file','data')",
                      "ATTACH DATABASE '/tmp/file' AS x", 'PRAGMA query_only=OFF', 'SELECT 1; SELECT 2;']:
            with self.subTest(query=query), self.assertRaises(ValueError):
                db.validate_read_sql(query)
        db.validate_read_sql("SELECT 'drop; .shell writefile' AS safe; -- DELETE")
        db.validate_read_sql('WITH sample AS (SELECT 1 AS n) SELECT * FROM sample;')


class MediaAndUITests(unittest.TestCase):
    def test_media_urls_and_redirects_stay_on_kakao_https(self):
        validate_url('https://talk.kakaocdn.net/image?key=private')
        for url in ['http://talk.kakaocdn.net/image', 'https://kakaocdn.net.evil.test/a',
                    'https://user:secret@talk.kakaocdn.net/a', 'file:///tmp/file', 'https://127.0.0.1/a']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_url(url)
        with self.assertRaises(ValueError):
            CDNRedirects().redirect_request(None, None, 302, '', {}, 'https://evil.test/a')

    def test_local_image_is_read_without_network_and_cached_privately(self):
        data = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / 'image.png'
            image.write_bytes(data)
            reader = SimpleNamespace(raw_message=lambda *args: {'type': 2, 'localFilePath': str(image), 'attachment': '{}'})
            with patch('kakaotalk_media.Path.home', return_value=root), patch('kakaotalk_media.build_opener') as network:
                result, content = retrieve(reader, 1, 1)
                network.assert_not_called()
                self.assertEqual(content, data)
                self.assertEqual(result['mimeType'], 'image/png')
                self.assertTrue(result['imageEmbeddable'])
                self.assertEqual(stat.S_IMODE(Path(result['path']).stat().st_mode), 0o600)
                with self.assertRaisesRegex(ValueError, 'max_bytes'):
                    retrieve(reader, 1, 1, max_bytes=len(data) - 1)
                image.unlink()
                cached, content = retrieve(reader, 1, 1, download=False)
                self.assertEqual(cached['source'], 'media_cache')
                self.assertEqual(content, data)

    def test_metadata_does_not_expose_urls_and_download_is_explicit(self):
        message = {'type': 2, 'attachment': json.dumps({'url': 'https://talk.kakaocdn.net/a?token=private'})}
        items = visible_media(media_entries(message))
        self.assertNotIn('private', json.dumps(items))
        reader = SimpleNamespace(raw_message=lambda *args: message)
        with patch('kakaotalk_media.build_opener') as network:
            result, content = retrieve(reader, 1, 1, download=False)
            network.assert_not_called()
        self.assertEqual(result['status'], 'not_downloaded')
        self.assertIsNone(content)

    def test_ui_draft_text_is_not_returned(self):
        payload = {'status': 'ok', 'windows': [{'title': '친구방', 'tree': {'role': 'AXWindow', 'children': [
            {'role': 'AXRow', 'children': [{'role': 'AXStaticText', 'value': '받은 메시지'}]},
            {'role': 'AXTextArea', 'editable': True, 'hasDraft': True, 'value': 'SECRET_DRAFT'}]}}]}
        with patch('kakaotalk_ui.sys.platform', 'darwin'), patch('kakaotalk_ui.shutil.which', return_value='/usr/bin/swift'), \
             patch('kakaotalk_ui.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(payload))):
            result = snapshot()
        self.assertNotIn('SECRET_DRAFT', json.dumps(result))
        self.assertTrue(result['windows'][0]['draftPresent'])
        self.assertEqual(result['windows'][0]['rows'][0]['texts'], ['받은 메시지'])

    def test_ui_timeout_is_a_status_and_does_not_crash_the_server(self):
        with patch('kakaotalk_ui.sys.platform', 'darwin'), patch('kakaotalk_ui.shutil.which', return_value='/usr/bin/swift'), \
             patch('kakaotalk_ui.subprocess.run', side_effect=subprocess.TimeoutExpired('swift', 12)):
            self.assertEqual(snapshot()['status'], 'timeout')


if __name__ == '__main__':
    unittest.main()
