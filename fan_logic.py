"""
Pure fan-control logic for the Legion Go 2 plugin.

This module contains ONLY hardware-independent math: tuning constants,
fan-curve lookup, and the smoothing algorithm. It has no dependency on
decky_plugin, /dev/port, or sysfs, so it can be imported and unit-tested
off-device (see test_smoothing.py).

main.py imports these so the running plugin and the test harness share the
exact same logic and can never drift apart.
"""

# --- Tuning Constants ---
PANIC_TEMP_C = 101
PANIC_MIN_RPM = 3800
MAX_RPM = 5200
MIN_ACTIVE_RPM = 1500

# Smoothing: Ramp-Rate Limiting
RAMP_UP_MAX = 300              # Max RPM increase per tick
RAMP_DOWN_MAX = 200            # Max RPM decrease per tick

# Smoothing: Hysteresis Thresholds
HYSTERESIS_UP_C = 3            # Respond to rising temp if moved +3C since last apply
HYSTERESIS_DOWN_C = 7          # Respond to falling temp if moved -7C since last apply
HOLD_TIMEOUT_TICKS = 3         # After this many holds, ramp anyway (prevents getting stuck)


def curve_lookup(temp, curve, sorted_temps, stepped):
    """
    Compute the ideal target RPM for a temperature given a fan curve.

    curve: dict of {temp: rpm}
    sorted_temps: pre-sorted list of curve keys (caller caches this)
    stepped: if True, use breakpoint steps; else linear interpolation

    Returns the ideal RPM, or -1 if the curve is empty.
    """
    if not sorted_temps:
        return -1

    if temp >= PANIC_TEMP_C:
        max_t = sorted_temps[-1]
        return max(PANIC_MIN_RPM, curve.get(max_t, PANIC_MIN_RPM))

    if stepped:
        ideal = -1
        for t in reversed(sorted_temps):
            if temp >= t:
                ideal = curve[t]
                break
        if ideal == -1:
            ideal = curve[sorted_temps[0]]
        return ideal

    if temp <= sorted_temps[0]:
        return curve[sorted_temps[0]]
    if temp >= sorted_temps[-1]:
        return curve[sorted_temps[-1]]

    for i in range(len(sorted_temps) - 1):
        t1, t2 = sorted_temps[i], sorted_temps[i + 1]
        if t1 <= temp <= t2:
            rpm1, rpm2 = curve[t1], curve[t2]
            fraction = (temp - t1) / (t2 - t1)
            return int(round(rpm1 + fraction * (rpm2 - rpm1)))
    return curve[sorted_temps[-1]]


def apply_smoothing(current_temp, last_applied_temp, hold_ticks,
                    ideal_target, current_target):
    """
    Apply ramp-rate limiting + hysteresis to determine the actual target RPM.

    Pure function: takes current smoothing state in, returns new state out.

    Returns a tuple:
      (new_target, new_last_applied_temp, new_hold_ticks, reason_string)

    If temp stays elevated/dropped but doesn't meet hysteresis for
    HOLD_TIMEOUT_TICKS consecutive ticks, ramp anyway to prevent getting stuck.
    """
    temp_delta = current_temp - last_applied_temp

    if ideal_target > current_target:
        if temp_delta >= HYSTERESIS_UP_C or hold_ticks >= HOLD_TIMEOUT_TICKS:
            new_target = min(ideal_target, current_target + RAMP_UP_MAX)
            if temp_delta >= HYSTERESIS_UP_C:
                reason = f"ramp_up: hysteresis met (+{temp_delta}C), {current_target}->{new_target}"
            else:
                reason = f"ramp_up: hold timeout ({HOLD_TIMEOUT_TICKS} ticks), {current_target}->{new_target}"
            return new_target, current_temp, 0, reason
        else:
            new_hold = hold_ticks + 1
            reason = f"hold: rising but hysteresis not met (delta={temp_delta}C, need +{HYSTERESIS_UP_C}C) [{new_hold}/{HOLD_TIMEOUT_TICKS}]"
            return current_target, last_applied_temp, new_hold, reason

    elif ideal_target < current_target:
        if temp_delta <= -HYSTERESIS_DOWN_C or hold_ticks >= HOLD_TIMEOUT_TICKS:
            new_target = max(ideal_target, current_target - RAMP_DOWN_MAX)
            if temp_delta <= -HYSTERESIS_DOWN_C:
                reason = f"ramp_down: hysteresis met ({temp_delta}C), {current_target}->{new_target}"
            else:
                reason = f"ramp_down: hold timeout ({HOLD_TIMEOUT_TICKS} ticks), {current_target}->{new_target}"
            return new_target, current_temp, 0, reason
        else:
            new_hold = hold_ticks + 1
            reason = f"hold: falling but hysteresis not met (delta={temp_delta}C, need -{HYSTERESIS_DOWN_C}C) [{new_hold}/{HOLD_TIMEOUT_TICKS}]"
            return current_target, last_applied_temp, new_hold, reason

    else:
        return current_target, last_applied_temp, 0, "hold: at target"
