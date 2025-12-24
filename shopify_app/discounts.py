import random

# Probability configuration
P_TRIGGER = 1 #.35  # 35% chance the lottery triggers
P_HIGH = 0.08     # high tier relative to triggered
P_MID = 0.40      # mid tier relative to triggered
P_LOW = 0.52      # low tier relative to triggered

# Discount ranges (percentages)
# High tier discounts are any whole number between 7% and 15%.
HIGH_RANGE = list(range(7, 16))
MID_RANGE = [5]
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
