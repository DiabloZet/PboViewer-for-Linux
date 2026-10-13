"""Ключи и подписи Bohemia (.biprivatekey / .bikey / .bisign) — аналог DSCreateKey и DSSignFile.

Подпись: три RSA-подписи (PKCS#1 v1.5, SHA1) от хэшей PBO:
    hash1 = SHA1 всего архива (то же, что записано в конце PBO)
    hash2 = SHA1(hash1 + namehash + префикс\\)
    hash3 = SHA1(filehash + namehash + префикс\\)
namehash — SHA1 имён файлов (в нижнем регистре, по алфавиту, без пустых файлов),
filehash — SHA1 содержимого файлов, отобранных по расширению (версия подписи 2 или 3).
"""

from __future__ import annotations

import hashlib
import math
import os
import secrets
import struct
from dataclasses import dataclass

from . import pbo as P

PUBLIC_MAGIC = b"\x06\x02\x00\x00\x00\x24\x00\x00RSA1"
PRIVATE_MAGIC = b"\x07\x02\x00\x00\x00\x24\x00\x00RSA2"

# v2: хэшируются все файлы, кроме этих расширений
V2_EXCLUDE = {"paa", "jpg", "p3d", "tga", "rvmat", "lip", "ogg", "wss", "png", "rtm", "pac", "fxy", "wrp"}
# v3: хэшируются только эти расширения
V3_INCLUDE = {"sqf", "inc", "bikb", "ext", "fsm", "sqm", "hpp", "cfg", "sqs", "h", "sqfc"}


class SignError(Exception):
    pass


# ---------------------------------------------------------------------------
# Ключи
# ---------------------------------------------------------------------------

def _le(n: int, size: int) -> bytes:
    return n.to_bytes(size, "little")


def _cstr(data: bytes, pos: int) -> tuple[str, int]:
    end = data.find(b"\0", pos)
    if end < 0:
        raise SignError("Файл ключа повреждён")
    return data[pos:end].decode("utf-8", errors="replace"), end + 1


@dataclass
class PublicKey:
    name: str
    bits: int
    e: int
    n: int

    def to_bytes(self) -> bytes:
        return (self.name.encode() + b"\0" + struct.pack("<I", self.bits // 8 + 20) + PUBLIC_MAGIC
                + struct.pack("<II", self.bits, self.e) + _le(self.n, self.bits // 8))

    @classmethod
    def from_bytes(cls, data: bytes) -> "PublicKey":
        name, pos = _cstr(data, 0)
        pos += 4
        if data[pos:pos + 12] != PUBLIC_MAGIC:
            raise SignError("Это не открытый ключ .bikey")
        bits, e = struct.unpack_from("<II", data, pos + 12)
        pos += 20
        n = int.from_bytes(data[pos:pos + bits // 8], "little")
        return cls(name, bits, e, n)

    def save(self, path: str) -> None:
        with open(path, "wb") as f:
            f.write(self.to_bytes())

    @classmethod
    def load(cls, path: str) -> "PublicKey":
        with open(path, "rb") as f:
            return cls.from_bytes(f.read())


@dataclass
class PrivateKey:
    name: str
    bits: int
    e: int
    n: int
    p: int
    q: int
    dp: int
    dq: int
    qinv: int
    d: int

    @property
    def public(self) -> PublicKey:
        return PublicKey(self.name, self.bits, self.e, self.n)

    def to_bytes(self) -> bytes:
        b8, b16 = self.bits // 8, self.bits // 16
        return (self.name.encode() + b"\0" + struct.pack("<I", b16 * 9 + 20) + PRIVATE_MAGIC
                + struct.pack("<II", self.bits, self.e) + _le(self.n, b8)
                + _le(self.p, b16) + _le(self.q, b16) + _le(self.dp, b16) + _le(self.dq, b16)
                + _le(self.qinv, b16) + _le(self.d, b8))

    @classmethod
    def from_bytes(cls, data: bytes) -> "PrivateKey":
        name, pos = _cstr(data, 0)
        pos += 4
        if data[pos:pos + 12] != PRIVATE_MAGIC:
            raise SignError("Это не закрытый ключ .biprivatekey")
        bits, e = struct.unpack_from("<II", data, pos + 12)
        pos += 20
        b8, b16 = bits // 8, bits // 16
        vals = []
        for size in (b8, b16, b16, b16, b16, b16, b8):
            chunk = data[pos:pos + size]
            if len(chunk) != size:
                raise SignError("Файл закрытого ключа обрывается")
            vals.append(int.from_bytes(chunk, "little"))
            pos += size
        return cls(name, bits, e, *vals)

    def save(self, path: str) -> None:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(self.to_bytes())

    @classmethod
    def load(cls, path: str) -> "PrivateKey":
        with open(path, "rb") as f:
            return cls.from_bytes(f.read())

    def sign_int(self, m: int) -> int:
        # CRT: быстрее, чем pow(m, d, n)
        m1 = pow(m, self.dp, self.p)
        m2 = pow(m, self.dq, self.q)
        h = (self.qinv * (m1 - m2)) % self.p
        return m2 + h * self.q


_SMALL_PRIMES = [p for p in range(3, 2000) if all(p % d for d in range(2, int(p ** 0.5) + 1))]


def _is_probable_prime(n: int, rounds: int = 40) -> bool:
    if n < 2:
        return False
    for p in _SMALL_PRIMES:
        if n % p == 0:
            return n == p
    d, s = n - 1, 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for _ in range(rounds):
        a = secrets.randbelow(n - 3) + 2
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(s - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _gen_prime(bits: int, e: int) -> int:
    while True:
        c = secrets.randbits(bits) | (1 << (bits - 1)) | (1 << (bits - 2)) | 1
        if math.gcd(c - 1, e) == 1 and _is_probable_prime(c):
            return c


def generate_key(name: str, bits: int = 1024, e: int = 65537) -> PrivateKey:
    """Аналог DSCreateKey: новая пара ключей RSA (по умолчанию 1024 бит, как у BI)."""
    if not name or any(c in name for c in "\0/\\"):
        raise SignError("Недопустимое имя ключа")
    while True:
        p = _gen_prime(bits // 2, e)
        q = _gen_prime(bits // 2, e)
        if p == q:
            continue
        if p < q:
            p, q = q, p
        n = p * q
        if n.bit_length() != bits:
            continue
        d = pow(e, -1, (p - 1) * (q - 1))
        return PrivateKey(name, bits, e, n, p, q, d % (p - 1), d % (q - 1), pow(q, -1, p), d)


# ---------------------------------------------------------------------------
# Хэши PBO
# ---------------------------------------------------------------------------

def _hashed_entries(archive: P.PboArchive) -> list[P.PboEntry]:
    return sorted(archive.entries, key=lambda e: P.to_pbo_path(e.name).lower())


def _raw_data(archive: P.PboArchive, e: P.PboEntry, f) -> bytes:
    """Данные записи в том виде, как они лежат в PBO (без распаковки)."""
    f.seek(e.offset)
    return f.read(e.data_size)


def pbo_hashes(archive: P.PboArchive, version: int = 3) -> tuple[bytes, bytes, bytes]:
    if version not in (2, 3):
        raise SignError("Версия подписи должна быть 2 или 3")
    ok = archive.verify()
    if ok is None:
        raise SignError("В PBO нет контрольной суммы SHA1 — пересохраните архив")
    if not ok:
        raise SignError("Контрольная сумма PBO не совпадает — архив повреждён")
    hash1 = archive.stored_checksum

    entries = _hashed_entries(archive)
    nh = hashlib.sha1()
    for e in entries:
        if e.data_size > 0:
            nh.update(P.to_pbo_path(e.name).lower().encode("utf-8"))
    namehash = nh.digest()

    fh = hashlib.sha1()
    nothing = True
    with open(archive.path, "rb") as f:
        for e in entries:
            ext = e.name.rsplit(".", 1)[-1].lower() if "." in e.basename else ""
            if version == 2 and ext in V2_EXCLUDE:
                continue
            if version == 3 and ext not in V3_INCLUDE:
                continue
            fh.update(_raw_data(archive, e, f))
            nothing = False
    if nothing:
        fh.update(b"nothing" if version == 2 else b"gnihton")
    filehash = fh.digest()

    prefix = archive.prefix or ""
    tail = b""
    if prefix:
        tail = prefix.encode("utf-8") + (b"" if prefix.endswith("\\") else b"\\")
    hash2 = hashlib.sha1(hash1 + namehash + tail).digest()
    hash3 = hashlib.sha1(filehash + namehash + tail).digest()
    return hash1, hash2, hash3


def _pad(h: bytes, size: int) -> int:
    # PKCS#1 v1.5 + DigestInfo SHA1
    digest_info = b"\x30\x21\x30\x09\x06\x05\x2b\x0e\x03\x02\x1a\x05\x00\x04\x14" + h
    block = b"\x00\x01" + b"\xff" * (size - 3 - len(digest_info)) + b"\x00" + digest_info
    return int.from_bytes(block, "big")


# ---------------------------------------------------------------------------
# Подпись
# ---------------------------------------------------------------------------

@dataclass
class Signature:
    key: PublicKey
    version: int
    sig1: int
    sig2: int
    sig3: int

    def to_bytes(self) -> bytes:
        size = self.key.bits // 8
        ln = struct.pack("<I", size)
        return (self.key.to_bytes() + ln + _le(self.sig1, size) + struct.pack("<I", self.version)
                + ln + _le(self.sig2, size) + ln + _le(self.sig3, size))

    @classmethod
    def from_bytes(cls, data: bytes) -> "Signature":
        key = PublicKey.from_bytes(data)
        pos = len(key.to_bytes())
        size = key.bits // 8
        sigs, version = [], 2
        for i in range(3):
            if i == 1:
                version = struct.unpack_from("<I", data, pos)[0]
                pos += 4
            (ln,) = struct.unpack_from("<I", data, pos)
            pos += 4
            sigs.append(int.from_bytes(data[pos:pos + ln], "little"))
            pos += ln
        if len(sigs) != 3 or ln != size:
            raise SignError("Файл .bisign повреждён")
        return cls(key, version, *sigs)

    @classmethod
    def load(cls, path: str) -> "Signature":
        with open(path, "rb") as f:
            return cls.from_bytes(f.read())


def bisign_path(pbo_path: str, key_name: str) -> str:
    return f"{pbo_path}.{key_name}.bisign"


def sign_pbo(pbo_path: str, key: PrivateKey, version: int = 3, dest: str = None) -> str:
    """Аналог DSSignFile: создаёт <файл>.pbo.<ключ>.bisign рядом с PBO. Возвращает путь."""
    archive = P.PboArchive.open(pbo_path)
    size = key.bits // 8
    sigs = [key.sign_int(_pad(h, size)) for h in pbo_hashes(archive, version)]
    sig = Signature(key.public, version, *sigs)
    dest = dest or bisign_path(archive.path, key.name)
    with open(dest, "wb") as f:
        f.write(sig.to_bytes())
    return dest


def verify_pbo(pbo_path: str, public: PublicKey, sig: Signature) -> bool:
    if public.n != sig.key.n or public.e != sig.key.e:
        return False
    archive = P.PboArchive.open(pbo_path)
    size = public.bits // 8
    for h, s in zip(pbo_hashes(archive, sig.version), (sig.sig1, sig.sig2, sig.sig3)):
        if pow(s, public.e, public.n) != _pad(h, size):
            return False
    return True
