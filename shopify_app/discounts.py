import random

# Probability configuration
P_TRIGGER = 0.35  # 35% chance the lottery triggers
P_HIGH = 0.05     # high tier relative probability
P_MID = 0.40      # mid tier relative probability
P_LOW = 0.55      # low tier relative probability

# Discount ranges (percentages)
HIGH_RANGE = [10, 15]
MID_RANGE = [5, 7]
LOW_RANGE = [3]

def select_discount_percentage():
    """Return a discount percentage or None if no discount is awarded."""
    if random.random() > P_TRIGGER:
        return None

    r = random.random()
    if r < P_HIGH:
        return random.choice(HIGH_RANGE)
    elif r < P_HIGH + P_MID:
        return random.choice(MID_RANGE)
    else:
        return random.choice(LOW_RANGE)
