"""
Driving Model and Kinematics for Predictive Maintenance Simulator.
Manages step-level driving dynamics, driver behavior profiles (Conservative, Normal, Aggressive),
driving trip profiles (City, Highway, Mixed), Poisson harsh-braking events, and diurnal ambient conditions.
"""

from enum import Enum
import math
from typing import Dict, Tuple, Optional
from datetime import datetime


class DriverBehavior(str, Enum):
    CONSERVATIVE = "CONSERVATIVE"
    NORMAL = "NORMAL"
    AGGRESSIVE = "AGGRESSIVE"


class DrivingProfile(str, Enum):
    CITY = "CITY"
    HIGHWAY = "HIGHWAY"
    MIXED = "MIXED"


class DrivingState(str, Enum):
    PARKED = "PARKED"
    STOPPED = "STOPPED"
    ACCELERATING = "ACCELERATING"
    CRUISING = "CRUISING"
    DECELERATING = "DECELERATING"


# Configuration parameters by driver behavior
BEHAVIOR_PARAMS = {
    DriverBehavior.CONSERVATIVE: {
        "accel_range": (1.0, 2.2),        # m/s^2
        "decel_range": (1.2, 2.5),        # m/s^2
        "speed_factor": 0.90,             # cruising speed multiplier
        "harsh_brake_lambda_per_hr": 0.05,# Poisson lambda per hour
        "high_load_prob": 0.02,
    },
    DriverBehavior.NORMAL: {
        "accel_range": (1.8, 3.2),
        "decel_range": (2.0, 3.8),
        "speed_factor": 1.00,
        "harsh_brake_lambda_per_hr": 0.20,
        "high_load_prob": 0.08,
    },
    DriverBehavior.AGGRESSIVE: {
        "accel_range": (2.8, 4.8),
        "decel_range": (3.2, 5.5),
        "speed_factor": 1.12,
        "harsh_brake_lambda_per_hr": 0.85,
        "high_load_prob": 0.25,
    },
}

# Configuration parameters by driving profile
PROFILE_TARGET_SPEEDS = {
    DrivingProfile.CITY: [0.0, 25.0, 35.0, 45.0, 50.0, 60.0],
    DrivingProfile.HIGHWAY: [70.0, 85.0, 95.0, 105.0, 115.0],
    DrivingProfile.MIXED: [0.0, 30.0, 50.0, 70.0, 85.0, 100.0],
}


class AmbientModel:
    @staticmethod
    def get_temperature(sim_time: datetime, rng_noise=None) -> float:
        """
        Diurnal temperature curve: T(t) = T_mean + Amplitude * sin(2*pi*(hour - 9) / 24)
        Ranges between ~21C (night/early morning) and ~35C (mid-afternoon).
        """
        hour = sim_time.hour + sim_time.minute / 60.0 + sim_time.second / 3600.0
        t_mean = 28.0
        amplitude = 7.0
        # Peak at 15:00 (hour 15: sin((15-9)*2pi/24) = sin(pi/2) = 1)
        base_temp = t_mean + amplitude * math.sin(2.0 * math.pi * (hour - 9.0) / 24.0)
        noise = float(rng_noise.normal(0.0, 0.4)) if rng_noise else 0.0
        return round(base_temp + noise, 2)


class VehicleDynamics:
    """
    Simulates physical movement for a single vehicle step.
    """
    def __init__(
        self,
        driver_behavior: DriverBehavior = DriverBehavior.NORMAL,
        driving_profile: DrivingProfile = DrivingProfile.MIXED,
        home_lat: float = 37.7749,
        home_lng: float = -122.4194,
    ):
        self.driver_behavior = driver_behavior
        self.driving_profile = driving_profile
        self.home_lat = home_lat
        self.home_lng = home_lng
        self.lat = home_lat
        self.lng = home_lng
        self.heading_rad = 0.0

        self.speed_kmh = 0.0
        self.target_speed_kmh = 0.0
        self.driving_state = DrivingState.PARKED
        self.engine_rpm = 800.0
        self.engine_load_pct = 0.0

    def step_trip(
        self,
        dt_seconds: float,
        rng_driving,
        rng_noise,
        brake_component=None,
    ) -> Dict:
        """
        Advances one step inside an active trip.
        Returns kinematics and stress data.
        """
        dt_hours = dt_seconds / 3600.0
        b_params = BEHAVIOR_PARAMS[self.driver_behavior]
        prev_speed = self.speed_kmh

        # 1. Harsh brake occurrence via Poisson
        lambda_step = b_params["harsh_brake_lambda_per_hr"] * dt_hours
        harsh_brake_count = int(rng_driving.poisson(lambda_step))
        high_speed_decel_count = 0
        is_harsh_brake = harsh_brake_count > 0

        if is_harsh_brake and self.speed_kmh > 30.0:
            if self.speed_kmh > 80.0:
                high_speed_decel_count += 1
            # Abrupt deceleration
            speed_drop = float(rng_driving.uniform(25.0, 45.0))
            if brake_component:
                # Worn brakes degrade stopping ability
                speed_drop = brake_component.compute_effective_deceleration(speed_drop)
            self.speed_kmh = max(0.0, self.speed_kmh - speed_drop)
            self.target_speed_kmh = float(rng_driving.choice([0.0, 20.0, 30.0]))
            self.driving_state = DrivingState.DECELERATING
        else:
            # Normal speed adjustment towards target speed
            if rng_driving.random() < (0.25 if self.driving_profile == DrivingProfile.CITY else 0.10):
                targets = PROFILE_TARGET_SPEEDS[self.driving_profile]
                base_target = float(rng_driving.choice(targets))
                self.target_speed_kmh = base_target * b_params["speed_factor"]

            speed_diff = self.target_speed_kmh - self.speed_kmh
            if abs(speed_diff) < 2.0:
                self.speed_kmh = self.target_speed_kmh
                self.driving_state = DrivingState.STOPPED if self.speed_kmh < 1.0 else DrivingState.CRUISING
            elif speed_diff > 0:
                self.driving_state = DrivingState.ACCELERATING
                accel_rate = float(rng_driving.uniform(*b_params["accel_range"])) * 3.6  # to km/h/s
                self.speed_kmh = min(self.target_speed_kmh, self.speed_kmh + accel_rate * dt_seconds)
            else:
                self.driving_state = DrivingState.DECELERATING
                if prev_speed > 80.0 and speed_diff < -20.0:
                    high_speed_decel_count += 1
                decel_rate = float(rng_driving.uniform(*b_params["decel_range"])) * 3.6  # to km/h/s
                if brake_component:
                    decel_rate = brake_component.compute_effective_deceleration(decel_rate)
                self.speed_kmh = max(self.target_speed_kmh, self.speed_kmh - decel_rate * dt_seconds)

        # Micro speed jitter when moving
        if self.speed_kmh > 5.0 and not is_harsh_brake:
            jitter = float(rng_noise.normal(0.0, 0.4))
            self.speed_kmh = max(0.0, self.speed_kmh + jitter)

        # Longitudinal acceleration in m/s^2
        longitudinal_accel = round((self.speed_kmh - prev_speed) / (3.6 * dt_seconds), 2)

        # Distance covered
        distance_km = (self.speed_kmh * dt_hours)

        # GPS coordinate progression
        if self.speed_kmh > 0.0:
            self.heading_rad += float(rng_driving.uniform(-0.08, 0.08))
            delta_deg = distance_km * 0.009
            self.lat += delta_deg * math.cos(self.heading_rad)
            self.lng += delta_deg * math.sin(self.heading_rad)

        # Engine load / power demand
        if self.speed_kmh < 1.0:
            self.engine_rpm = 750.0 + float(rng_noise.normal(0.0, 15.0))
            self.engine_load_pct = 12.0 + float(rng_noise.normal(0.0, 2.0))
        elif self.driving_state == DrivingState.ACCELERATING:
            self.engine_rpm = min(6000.0, 1800.0 + self.speed_kmh * 35.0 + float(rng_noise.normal(0.0, 50.0)))
            self.engine_load_pct = min(100.0, 55.0 + longitudinal_accel * 12.0 + float(rng_noise.normal(0.0, 5.0)))
        else:
            self.engine_rpm = min(4000.0, 1200.0 + self.speed_kmh * 22.0)
            self.engine_load_pct = min(80.0, 30.0 + (self.speed_kmh / 120.0) * 30.0)

        return {
            "speed_kmh": round(max(0.0, self.speed_kmh), 2),
            "longitudinal_accel": longitudinal_accel,
            "distance_km": distance_km,
            "harsh_brake_count": harsh_brake_count,
            "high_speed_decel_count": high_speed_decel_count,
            "is_harsh_brake": is_harsh_brake,
            "driving_state": self.driving_state.value,
            "engine_rpm": round(max(600.0, self.engine_rpm), 1),
            "engine_load_pct": round(max(5.0, min(100.0, self.engine_load_pct)), 1),
            "lat": round(self.lat, 6),
            "lng": round(self.lng, 6),
        }

    def step_parked(self, home_lat: float, home_lng: float) -> Dict:
        """
        Advances one step when vehicle is parked (idle between trips).
        """
        self.speed_kmh = 0.0
        self.target_speed_kmh = 0.0
        self.driving_state = DrivingState.PARKED
        self.engine_rpm = 0.0
        self.engine_load_pct = 0.0
        self.lat = home_lat
        self.lng = home_lng

        return {
            "speed_kmh": 0.0,
            "longitudinal_accel": 0.0,
            "distance_km": 0.0,
            "harsh_brake_count": 0,
            "high_speed_decel_count": 0,
            "is_harsh_brake": False,
            "driving_state": DrivingState.PARKED.value,
            "engine_rpm": 0.0,
            "engine_load_pct": 0.0,
            "lat": round(self.lat, 6),
            "lng": round(self.lng, 6),
        }

    def get_state(self) -> dict:
        return {
            "speed_kmh": self.speed_kmh,
            "target_speed_kmh": self.target_speed_kmh,
            "driving_state": self.driving_state.value,
            "engine_rpm": self.engine_rpm,
            "engine_load_pct": self.engine_load_pct,
            "lat": self.lat,
            "lng": self.lng,
            "heading_rad": self.heading_rad,
        }

    def set_state(self, state: dict):
        self.speed_kmh = state.get("speed_kmh", 0.0)
        self.target_speed_kmh = state.get("target_speed_kmh", 0.0)
        if "driving_state" in state:
            self.driving_state = DrivingState(state["driving_state"])
        self.engine_rpm = state.get("engine_rpm", 800.0)
        self.engine_load_pct = state.get("engine_load_pct", 0.0)
        self.lat = state.get("lat", self.lat)
        self.lng = state.get("lng", self.lng)
        self.heading_rad = state.get("heading_rad", 0.0)

