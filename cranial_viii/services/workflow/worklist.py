"""
Daily Clinical Worklist & Priority Queue
Generates role-tailored daily worklists for Clinic Staff, Audiologists, and Physicians.
Categorizes items by clinical urgency (STAT, High, Routine).
"""

from typing import Dict, List, Optional
from datetime import datetime, date
from dataclasses import dataclass, asdict


@dataclass
class WorklistItem:
    id: str
    priority: str       # 'STAT', 'HIGH', 'ROUTINE'
    role: str           # 'PHYSICIAN', 'AUDIOLOGIST', 'MA_CLERK', 'BILLING'
    category: str       # 'UNSIGNED_NOTE', 'CONFLICT_QUEUE', 'OVERDUE_FAX', 'DELAYED_VNG', 'ROI_REQUEST'
    title: str
    description: str
    patient_identifier: str
    created_at: str
    due_in: str


class WorklistManager:
    def __init__(self):
        self.items: List[WorklistItem] = []

    def add_item(self, item: WorklistItem):
        self.items.append(item)

    def get_by_role(self, role: Optional[str] = None) -> List[Dict]:
        """Returns worklist sorted by priority (STAT first, then HIGH, then ROUTINE)."""
        priority_order = {"STAT": 0, "HIGH": 1, "ROUTINE": 2}
        filtered = [it for it in self.items if role is None or it.role == role]
        sorted_items = sorted(filtered, key=lambda x: priority_order.get(x.priority, 3))
        return [asdict(it) for it in sorted_items]

    def summary_counts(self) -> Dict[str, int]:
        return {
            "total": len(self.items),
            "stat": sum(1 for it in self.items if it.priority == "STAT"),
            "high": sum(1 for it in self.items if it.priority == "HIGH"),
            "routine": sum(1 for it in self.items if it.priority == "ROUTINE"),
        }


worklist_manager = WorklistManager()
