from dataclasses import dataclass
from datetime import datetime


@dataclass
class FrozenClock:
    value: datetime

    def now(self) -> datetime:
        return self.value
