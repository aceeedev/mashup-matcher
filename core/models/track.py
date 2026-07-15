from enum import Enum

from pydantic import BaseModel


class CamelotKey(str, Enum):
    A1 = "1A"
    A2 = "2A"
    A3 = "3A"
    A4 = "4A"
    A5 = "5A"
    A6 = "6A"
    A7 = "7A"
    A8 = "8A"
    A9 = "9A"
    A10 = "10A"
    A11 = "11A"
    A12 = "12A"
    B1 = "1B"
    B2 = "2B"
    B3 = "3B"
    B4 = "4B"
    B5 = "5B"
    B6 = "6B"
    B7 = "7B"
    B8 = "8B"
    B9 = "9B"
    B10 = "10B"
    B11 = "11B"
    B12 = "12B"

class TrackData(BaseModel):
    title: str
    artist: str
    bpm: float
    key: CamelotKey
    source: str
