"""Local EML archive viewer.

Indexes .eml files in place on a (network) drive. The emails themselves are
never copied or moved; a SQLite index plus tags live next to the program
(so a copy on a NAS is shared by every PC that launches it).
"""

__version__ = "1.2.0"
