"""
HIPAA-Compliant Security & Key Encryption (KEK) Layer
Handles encryption of sensitive clinical identifiers and audit token generation.
"""

import os
from pathlib import Path
from cryptography.fernet import Fernet
from config.settings import settings


class KeyManager:
    def __init__(self, kek_path: str = None):
        self.kek_path = Path(kek_path or settings.KEK_PATH)
        self._cipher = None

    def _ensure_key(self):
        if self._cipher is not None:
            return
        
        self.kek_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.kek_path.exists():
            # Generate new KEK
            key = Fernet.generate_key()
            self.kek_path.write_bytes(key)
            os.chmod(self.kek_path, 0o600)  # Owner read/write only
            self._cipher = Fernet(key)
        else:
            key = self.kek_path.read_bytes()
            self._cipher = Fernet(key)

    def encrypt_phi(self, plaintext: str) -> str:
        """Encrypts sensitive clinical string (e.g. SSN, raw notes)."""
        self._ensure_key()
        if not plaintext:
            return ""
        return self._cipher.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt_phi(self, ciphertext: str) -> str:
        """Decrypts encrypted clinical string."""
        self._ensure_key()
        if not ciphertext:
            return ""
        return self._cipher.decrypt(ciphertext.encode("utf-8")).decode("utf-8")


key_manager = KeyManager()
