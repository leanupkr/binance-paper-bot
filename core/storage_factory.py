"""
core/storage_factory.py — 스토리지 백엔드 팩토리.
STORAGE_BACKEND 환경변수로 sqlite(기본) / supabase 선택.
"""
from __future__ import annotations

import os

from core.config import AppConfig


def create_storage(config: AppConfig):
    """
    환경변수 STORAGE_BACKEND 에 따라 저장소 인스턴스 반환.
    - "supabase": SupabaseStorage(SUPABASE_URL, SUPABASE_SERVICE_KEY) — REST 백엔드.
      둘 중 하나라도 없으면 ValueError.
    - 그 외(기본 "sqlite"): Storage(config.db_path).
    반환 객체는 initialize() 로 초기화 가능.
    """
    backend = os.environ.get("STORAGE_BACKEND", "sqlite").lower()

    if backend == "supabase":
        url = os.environ.get("SUPABASE_URL", "")
        service_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
        if not url or not service_key:
            raise ValueError(
                "STORAGE_BACKEND=supabase 인데 SUPABASE_URL / SUPABASE_SERVICE_KEY "
                "환경변수가 없습니다. .env 또는 GitHub Actions secrets 에 설정하세요."
            )
        from core.supabase_store import SupabaseStorage
        return SupabaseStorage(url, service_key)

    # 기본: SQLite
    from core.storage import Storage
    return Storage(config.db_path)
