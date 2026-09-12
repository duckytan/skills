# -*- coding: utf-8 -*-
"""Unit tests for the P0 carve fix in header.analyze() (伪装包误判为 plain).

Run:  python -m unittest tests.test_header_carve -v   (from scripts/)
Pure stdlib; no network, no real batches, no db.

Cases (终审规格):
  1. MZ head + 2KB junk + Rar!5 signature @2048  -> carve candidate
  2. ftyp head + zip signature @1MB              -> carve candidate
  3. clean mp4 (ftyp + zeros)                    -> still "plain MP4 file"
  4. real zip at offset 0                        -> is_archive, untouched
  5. UA head tamper                              -> patch_from route untouched
  6. TXT file                                    -> no carve (legacy rule)
  7. carve candidate -> repair_artifacts() still yields a _carved.* artifact
  8. TXT / huge-junk without signature           -> UNKNOWN_BINARY (unchanged)
"""

import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline_lib import config as C          # noqa: E402
from pipeline_lib import header               # noqa: E402

RAR5_SIG = b"Rar!\x1a\x07\x01\x00"
ZIP_SIG = b"PK\x03\x04"
FTYP = b"\x00\x00\x00\x18ftypmp42"


class CarveAnalyzeTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dae_carve_test_")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _file(self, name, blob):
        p = os.path.join(self.dir, name)
        with open(p, "wb") as fh:
            fh.write(blob)
        return p

    def test_1_mz_head_with_rar5_signature_at_2k(self):
        """① MZ 头 + 2KB 垃圾 + Rar!5 签名@2048 → 判定 carve 候选."""
        blob = b"MZ" + b"\x00" * 2046 + RAR5_SIG + b"payload" * 4096
        p = self._file("杂役,txt.exe", blob)
        info = header.analyze(p)
        self.assertEqual(info.sig_offset, 2048)
        self.assertFalse(info.is_archive)
        self.assertIsNone(info.patch_from)
        self.assertIn("embedded archive signature", info.skip_reason)
        self.assertEqual(info.fail_reason, "")          # not a plain skip
        self.assertNotEqual(info.skip_reason, "plain EXE file")

    def test_2_ftyp_head_with_zip_signature_at_1mb(self):
        """② ftyp 头 + zip 签名@1MB → carve 候选（64MB 内但在深处）."""
        blob = FTYP + b"\x00" * (1024 * 1024 - len(FTYP)) + ZIP_SIG \
            + b"z" * (C.CARVE_MIN_PAYLOAD_BYTES + 16)
        p = self._file("嵌套视频.mp4", blob)
        info = header.analyze(p)
        self.assertEqual(info.sig_offset, 1024 * 1024)
        self.assertFalse(info.is_archive)
        self.assertEqual(info.real_type, "MP4")
        self.assertIn("embedded archive signature", info.skip_reason)

    def test_3_clean_mp4_still_plain(self):
        """③ 纯净 mp4（ftyp+全零）→ 仍判 plain MP4 跳过，不误报 carve."""
        blob = FTYP + b"\x00" * 65536
        p = self._file("普通.mp4", blob)
        info = header.analyze(p)
        self.assertEqual(info.real_type, "MP4")
        self.assertFalse(info.is_archive)
        self.assertEqual(info.sig_offset, 0)
        self.assertEqual(info.skip_reason, "plain MP4 file")

    def test_4_real_zip_at_head_untouched(self):
        """④ 真 zip 在头部 → is_archive=True 不受影响."""
        blob = ZIP_SIG + b"\x00" * 2048
        p = self._file("真.zip", blob)
        info = header.analyze(p)
        self.assertTrue(info.is_archive)
        self.assertEqual(info.real_type, "ZIP")
        self.assertEqual(info.sig_offset, 0)

    def test_5_ua_head_tamper_untouched(self):
        """⑤ UA 头篡改 → patch_from 路径不受影响."""
        blob = b"UA" + b"\x03\x04" + b"\x00" * 4096
        p = self._file("被篡改.dat", blob)
        info = header.analyze(p)
        self.assertEqual(info.patch_from, b"UA")
        self.assertEqual(info.real_type, "ZIP")
        self.assertFalse(info.is_archive)

    def test_6_txt_never_carved(self):
        """⑥ 文本文件维持现状：不做 carve，直接 plain TXT."""
        blob = (u"这是一个说明文件，全部可见文本没有二进制内容。"
                u"plain ascii too 12345\n").encode("utf-8") * 4
        p = self._file("说明.txt", blob)
        info = header.analyze(p)
        self.assertEqual(info.real_type, "TXT")
        self.assertEqual(info.skip_reason, "plain TXT file")
        self.assertEqual(info.sig_offset, 0)

    def test_7_carve_candidate_yields_carved_artifact(self):
        """⑦ carve 候选 → repair_artifacts() 产出 _carved.<ext>（链路兼容）."""
        payload = RAR5_SIG + b"rar-payload" * 4096
        blob = b"MZ" + b"\x00" * 2046 + payload
        p = self._file("伪装.exe", blob)
        info = header.analyze(p)
        self.assertEqual(info.sig_offset, 2048)
        row = {"path": p, "size_bytes": len(blob)}   # sqlite3.Row-like access
        arts = header.repair_artifacts(row, info)
        self.assertEqual(len(arts), 1)
        path, kind = arts[0]
        self.assertEqual(kind, "CARVED")
        self.assertTrue(path.endswith("_carved.rar"))
        self.assertTrue(os.path.isfile(path))
        with open(path, "rb") as fh:
            self.assertTrue(fh.read(len(RAR5_SIG)) == RAR5_SIG)

    def test_8_unknown_binary_without_signature_unchanged(self):
        """⑧ 未知二进制且无签名 → 仍 UNKNOWN_BINARY（行为不变）."""
        blob = bytes(range(256)) * 64          # binary noise, no text, no magic
        p = self._file("乱.bin", blob)
        info = header.analyze(p)
        self.assertEqual(info.fail_reason, C.FAIL_UNKNOWN_BINARY)
        self.assertEqual(info.skip_reason, "no known signature")


    def test_9_earliest_signature_wins_zip_over_nested_7z(self):
        """⑨ P0-2: zip 签名在前、7z 签名在后 45 字节 → carve 必须指向 zip.

        真实批次 P88-二重积分.mp4 场景：zip 外层容器 + 内部第一个成员是 7z，
        按列表顺序（7z 先）会切出内层成员 → _carved.7z 全部 ARCHIVE_CORRUPT。
        """
        zip_off = 200
        sevenz_off = zip_off + 45        # zip 签名(4B)后 45 字节
        blob = bytearray(FTYP + b"\xAB" * (zip_off - len(FTYP)))
        self.assertEqual(len(blob), zip_off)
        blob += ZIP_SIG                                   # @200
        blob += b"\xAB" * (sevenz_off - len(blob))        # 填到 245
        blob += b"7z\xbc\xaf\x27\x1c"                     # 7z 签名 @245
        blob += b"\xCD" * (C.CARVE_MIN_PAYLOAD_BYTES + 4096)
        p = self._file("P88-二重积分.mp4", bytes(blob))
        info = header.analyze(p)
        self.assertEqual(info.sig_offset, zip_off)        # 200, NOT 245
        self.assertFalse(info.is_archive)
        self.assertEqual(info.real_type, "MP4")           # 容器类型语义不变
        self.assertIn("embedded archive signature at offset 200",
                      info.skip_reason)
        # 产物级验证：CARVED 分支用 zip 签名定扩展名 → _carved.zip
        row = {"path": p, "size_bytes": len(blob)}
        arts = header.repair_artifacts(row, info)
        self.assertEqual(len(arts), 1)
        self.assertTrue(arts[0][0].endswith("_carved.zip"), arts[0][0])
        self.assertEqual(arts[0][1], "CARVED")

    def test_10_only_7z_signature_unchanged(self):
        """⑩ 只有 7z 签名时行为不变（无 zip 可竞争）."""
        blob = FTYP + b"\xAB" * (2048 - len(FTYP)) \
            + b"7z\xbc\xaf\x27\x1c" \
            + b"\xCD" * (C.CARVE_MIN_PAYLOAD_BYTES + 4096)
        p = self._file("只有7z.mp4", blob)
        info = header.analyze(p)
        self.assertEqual(info.sig_offset, 2048)
        self.assertFalse(info.is_archive)
        self.assertIn("embedded archive signature", info.skip_reason)


if __name__ == "__main__":
    unittest.main(verbosity=2)
