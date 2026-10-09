"""
Offline Sovereign LLM Client for CranialVIII
Connects directly to local Ollama or llama.cpp instance on 127.0.0.1.
Ensures zero PHI leaves the clinic premises, with $0 cloud API costs.
"""

from typing import List, Dict, Optional, Generator
import httpx
from config.settings import settings


class OfflineLLMClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None
    ):
        self.base_url = (base_url or settings.OFFLINE_AI_BASE_URL).rstrip("/")
        self.model = model or settings.OFFLINE_AI_MODEL
        self.timeout = 120.0  # Allow time for deep clinical reasoning on local CPU/GPU

    async def check_health(self) -> Dict:
        """Verifies local offline model runner is operational."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                # Test OpenAI-compatible models endpoint
                resp = await client.get(f"{self.base_url}/models")
                if resp.status_code == 200:
                    return {"status": "online", "models": resp.json()}
                return {"status": "error", "code": resp.status_code}
        except Exception as e:
            return {"status": "offline", "error": str(e), "url": self.base_url}

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.1,  # Low temperature for clinical rigor
        max_tokens: int = 2048
    ) -> str:
        """Sends prompt to local offline model and returns response text."""
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False
        }
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {settings.OFFLINE_AI_API_KEY}"},
                    json=payload
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return data["choices"][0]["message"]["content"]
                else:
                    return f"[Local LLM Error {resp.status_code}]: {resp.text}"
        except Exception as e:
            return f"[Local Connection Error]: Ensure Ollama/llama.cpp is running on {self.base_url}. Details: {e}"


offline_ai = OfflineLLMClient()
