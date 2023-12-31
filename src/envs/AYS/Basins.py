"""
This enumeration is used to differ between the three possible outcomes in our model.
1. The agent can leave the planetary boundaries (OUT_PB)
2. It can stay within the boundaries but in an unsustainable state (BROWN_FP)
3. It can stay within the boundaries and within a sustainable state (GREEN_FP)

@author: Felix Strnad
"""
from enum import Enum
class Basins(Enum):
    OUT_PB = 0
    BLACK_FP = 1
    GREEN_FP = 2

    A_PB = 3
    Y_SF = 4
    S_PB = 5

    OUT_OF_TIME = 6