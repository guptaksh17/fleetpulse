"""
Fleet Population Generator and Vehicle Model for FleetPulse Simulator.
Manages realistic fleet demographics (ICE/EV/Hybrid, driver behaviors, driving profiles,
initial age and wear distribution) and vehicle execution lifecycle.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Dict, List, Optional, Tuple
import uuid

from .vin import generate_vin
from .driving import (
    DriverBehavior,
    DrivingProfile,
    DrivingState,
    VehicleDynamics,
    AmbientModel,
)
from .trips import TripScheduler, TripRecord
from .faults import FaultManager
from .components import (
    LifecycleState,
    MaintenanceEventRecord,
    BrakeComponent,
    PowertrainComponent,
    BatteryComponent,
)

# Vehicle models by powertrain
VEHICLE_CATALOG = {
    "ICE": [
        ("Ford", "Transit 350"),
        ("Freightliner", "Cascadia"),
        ("Chevrolet", "Express 2500"),
        ("Ram", "ProMaster 2500"),
    ],
    "EV": [
        ("Tesla", "Model 3"),
        ("Rivian", "EDV-700"),
        ("Ford", "E-Transit"),
        ("Mercedes-Benz", "eSprinter"),
    ],
    "HYBRID": [
        ("Toyota", "Prius"),
        ("Ford", "Maverick Hybrid"),
        ("Toyota", "Sienna Hybrid"),
    ],
}

# Major depot coordinates
DEPOT_COORDINATES = [
    (37.7749, -122.4194),   # San Francisco / Bay Area
    (34.0522, -118.2437),   # Los Angeles
    (32.7767, -96.7970),    # Dallas / Fort Worth
    (41.8781, -87.6298),    # Chicago
    (40.7128, -74.0060),    # New York / New Jersey
]


def assign_oem(vin: str, config) -> str:
    """Deterministic OEM assignment: OEM_B for oem_b_percent of VINs (by hash), else the configured OEM."""
    import hashlib

    pct = getattr(config.population, "oem_b_percent", 0.0) or 0.0
    if pct > 0 and int(hashlib.sha256(vin.encode()).hexdigest(), 16) % 100 < pct:
        return "OEM_B"
    return config.population.oem


def build_components(config, v_type: str, brake_wear: float, pt_wear: float, batt_wear: float, wear_mult: float) -> Dict[str, object]:
    """Instantiates component models with every degradation parameter taken from config."""
    deg = config.degradation
    eff = deg.effects
    common = dict(
        wear_rate_multiplier=wear_mult,
        service_delay_range=tuple(deg.service_delay_hours),
        service_recovery_range=tuple(deg.service_recovery_factor),
        thresholds=deg.threshold_tuple,
    )
    components = {
        "BRAKE": BrakeComponent(
            initial_health=100.0 - brake_wear,
            base_wear_per_hour=deg.base_wear_per_hour["BRAKE"],
            sudden_failure_per_hour_at_risk=deg.sudden_failure_per_hour_at_risk["BRAKE"],
            decel_deficit_at_full_wear=eff["brake_decel_deficit_at_full_wear"],
            thermal_mass_loss_at_full_wear=eff["brake_thermal_mass_loss_at_full_wear"],
            **common,
        ),
        "POWERTRAIN": PowertrainComponent(
            vehicle_type=v_type,
            initial_health=100.0 - pt_wear,
            base_wear_per_hour=deg.base_wear_per_hour["POWERTRAIN"],
            sudden_failure_per_hour_at_risk=deg.sudden_failure_per_hour_at_risk["POWERTRAIN"],
            temp_offset_per_wear_c=eff["powertrain_temp_offset_per_wear_c"],
            consumption_increase_at_full_wear=eff["powertrain_consumption_increase_at_full_wear"],
            **common,
        ),
    }
    if v_type in ("EV", "HYBRID"):
        components["BATTERY"] = BatteryComponent(
            initial_health=100.0 - batt_wear,
            base_wear_per_hour=deg.base_wear_per_hour["BATTERY"],
            sudden_failure_per_hour_at_risk=deg.sudden_failure_per_hour_at_risk["BATTERY"],
            retention_loss_at_full_wear=eff["battery_retention_loss_at_full_wear"],
            resistance_rise_at_full_wear=eff["battery_resistance_rise_at_full_wear"],
            **common,
        )
    return components


class SimVehicle:
    def __init__(
        self,
        vehicle_id: str,
        fleet_id: str,
        tenant_id: str,
        driver_id: Optional[str],
        vin: str,
        oem_id: str,
        vehicle_type: str,
        make: str,
        model: str,
        year: int,
        driver_behavior: DriverBehavior,
        driving_profile: DrivingProfile,
        age_years: float,
        initial_odometer_km: float,
        wear_rate_multiplier: float,
        home_lat: float,
        home_lng: float,
        components: Dict[str, object],
        vehicle_component_ids: Dict[str, str],
        seq: int = 0,
        injected_dtc_code: Optional[str] = None,
        inject_after_s: float = 0.0,
        inject_duration_s: float = 0.0,
    ):
        self.vehicle_id = vehicle_id
        self.fleet_id = fleet_id
        self.tenant_id = tenant_id
        self.driver_id = driver_id
        self.vin = vin
        self.oem_id = oem_id
        self.vehicle_type = vehicle_type.upper()
        self.make = make
        self.model = model
        self.year = year
        self.driver_behavior = driver_behavior
        self.driving_profile = driving_profile
        self.age_years = age_years
        self.odometer_km = initial_odometer_km
        self.wear_rate_multiplier = wear_rate_multiplier
        self.home_lat = home_lat
        self.home_lng = home_lng

        self.components = components
        self.vehicle_component_ids = vehicle_component_ids
        self.component_scale: Dict[str, float] = {}
        self.seq = seq

        self.dynamics = VehicleDynamics(
            driver_behavior=driver_behavior,
            driving_profile=driving_profile,
            home_lat=home_lat,
            home_lng=home_lng,
        )
        self.trip_scheduler = TripScheduler(
            vehicle_id=vehicle_id,
            driving_profile=driving_profile,
            driver_id=driver_id,
        )
        self.fault_manager = FaultManager(
            transient_rate_per_hour=0.001,
            injected_code=injected_dtc_code,
            inject_after_seconds=inject_after_s,
            inject_duration_seconds=inject_duration_s,
        )

        # Powertrain dynamic states
        self.battery_soc_pct: float = 90.0
        self.fuel_level_pct: float = 85.0
        self.is_charging: bool = False
        self.coolant_temp_c: float = 85.0
        self.motor_temp_c: float = 45.0

    def step(
        self,
        dt_seconds: float,
        current_sim_time: datetime,
        rng_driving,
        rng_degradation,
        rng_faults,
        rng_noise,
        scheduled_slots: List[Tuple[datetime, datetime]],
        degradation_scale: float = 1.0,
        elapsed_seconds: float = 0.0,
    ) -> Tuple[dict, List[MaintenanceEventRecord], Optional[TripRecord]]:
        """
        Advances the vehicle through one simulation step.
        Returns:
            telemetry_data (dict): Observable signals (no hidden state!)
            new_maintenance_events (List[MaintenanceEventRecord])
            completed_trip (Optional[TripRecord])
        """
        self.seq += 1
        dt_hours = dt_seconds / 3600.0
        ambient_c = AmbientModel.get_temperature(current_sim_time, rng_noise)

        brake_comp: BrakeComponent = self.components.get("BRAKE")
        pt_comp: PowertrainComponent = self.components.get("POWERTRAIN")
        batt_comp: Optional[BatteryComponent] = self.components.get("BATTERY")

        # Check vehicle operational status
        # If any component is in FAILURE, vehicle cannot drive
        is_vehicle_failed = any(
            c.lifecycle == LifecycleState.FAILURE for c in self.components.values()
        )

        # 1. Driving and Trips
        if not is_vehicle_failed:
            is_in_trip, current_trip_id, completed_trip = self.trip_scheduler.check_trip_status(
                current_sim_time,
                step_distance_km=0.0,  # updated after dynamics
                scheduled_slots=scheduled_slots,
            )
        else:
            is_in_trip = False
            current_trip_id = None
            completed_trip = None

        if is_in_trip:
            self.is_charging = False
            k_data = self.dynamics.step_trip(
                dt_seconds=dt_seconds,
                rng_driving=rng_driving,
                rng_noise=rng_noise,
                brake_component=brake_comp,
            )
            # Update trip distance
            self.trip_scheduler.current_trip_distance_km += k_data["distance_km"]
        else:
            k_data = self.dynamics.step_parked(self.home_lat, self.home_lng)
            # EV/Hybrid charging logic when parked at home depot
            if batt_comp and self.battery_soc_pct < 85.0:
                self.is_charging = True
                charge_rate_kw = 11.0  # Level 2 AC charge rate
                energy_added = charge_rate_kw * dt_hours
                # Slow down charging above 80% SoC when aged
                retention = batt_comp.capacity_retention
                charge_eff = 0.90 if self.battery_soc_pct < 80.0 else (0.80 * retention)
                self.battery_soc_pct = min(100.0, self.battery_soc_pct + (energy_added * charge_eff / batt_comp.nominal_capacity_kwh) * 100.0)
            else:
                self.is_charging = False

        self.odometer_km += k_data["distance_km"]

        # 2. Thermal Dynamics & Component Stresses
        brake_temp = ambient_c
        if brake_comp:
            brake_temp = brake_comp.update_temperature(
                ambient_c=ambient_c,
                speed_kmh=k_data["speed_kmh"],
                decel_magnitude=abs(min(0.0, k_data["longitudinal_accel"])),
                harsh_brake=k_data["is_harsh_brake"],
                dt_seconds=dt_seconds,
            )

        # Powertrain thermal & load
        pt_wear_offset = pt_comp.compute_temp_offset() if pt_comp else 0.0
        consumption_mult = pt_comp.compute_consumption_multiplier() if pt_comp else 1.0

        engine_temp_c = None
        motor_temp_c = None
        engine_rpm = None
        engine_load_pct = None
        power_kw = None

        if self.vehicle_type == "ICE":
            engine_rpm = k_data["engine_rpm"]
            engine_load_pct = k_data["engine_load_pct"]
            if k_data["speed_kmh"] > 0:
                target_temp = 90.0 + (engine_load_pct / 100.0) * 15.0 + pt_wear_offset
                self.coolant_temp_c += (target_temp - self.coolant_temp_c) * 0.1
                # Fuel burn
                fuel_burned = (k_data["distance_km"] * 0.08 * consumption_mult)
                self.fuel_level_pct = max(5.0, self.fuel_level_pct - fuel_burned)
            else:
                self.coolant_temp_c += (ambient_c - self.coolant_temp_c) * 0.02
            engine_temp_c = round(self.coolant_temp_c + float(rng_noise.normal(0.0, 0.5)), 1)
            power_kw = round((engine_rpm * (engine_load_pct / 100.0) * 180.0) / 9549.0, 1)

        elif self.vehicle_type == "EV":
            if k_data["speed_kmh"] > 0:
                power_demand = (k_data["speed_kmh"] / 100.0) * 35.0 + max(0.0, k_data["longitudinal_accel"]) * 20.0
                power_kw = round(power_demand, 1)
                target_motor = 45.0 + (power_kw / 50.0) * 30.0 + pt_wear_offset
                self.motor_temp_c += (target_motor - self.motor_temp_c) * 0.1
                # Energy draw
                batt_kwh = (power_kw * dt_hours * consumption_mult)
                if batt_comp:
                    self.battery_soc_pct = max(2.0, self.battery_soc_pct - (batt_kwh / batt_comp.nominal_capacity_kwh) * 100.0)
            else:
                self.motor_temp_c += (ambient_c - self.motor_temp_c) * 0.03
                power_kw = -11.0 if self.is_charging else 0.0
            motor_temp_c = round(self.motor_temp_c + float(rng_noise.normal(0.0, 0.4)), 1)

        else:  # HYBRID
            engine_rpm = k_data["engine_rpm"] if k_data["speed_kmh"] > 40.0 else 0.0
            engine_load_pct = k_data["engine_load_pct"] if engine_rpm > 0 else 0.0
            power_kw = round((k_data["speed_kmh"] / 100.0) * 25.0, 1)
            if engine_rpm > 0:
                self.coolant_temp_c += (90.0 + pt_wear_offset - self.coolant_temp_c) * 0.1
                engine_temp_c = round(self.coolant_temp_c + float(rng_noise.normal(0.0, 0.5)), 1)
            else:
                self.coolant_temp_c += (ambient_c - self.coolant_temp_c) * 0.02
            # Traction motor follows electrical load with the same lag model as the EV path
            if k_data["speed_kmh"] > 0:
                target_motor = 45.0 + (power_kw / 50.0) * 30.0 + pt_wear_offset
                self.motor_temp_c += (target_motor - self.motor_temp_c) * 0.1
            else:
                self.motor_temp_c += (ambient_c - self.motor_temp_c) * 0.03
            motor_temp_c = round(self.motor_temp_c + float(rng_noise.normal(0.0, 0.4)), 1)

        # Battery Pack observable parameters
        battery_level = None
        battery_voltage = None
        battery_current = None
        battery_temp = None

        if batt_comp:
            apparent_soc = batt_comp.compute_apparent_soc(self.battery_soc_pct)
            battery_level = round(apparent_soc, 1)
            # Nominal pack voltage: 400V
            nominal_pack_v = 400.0 * (0.90 + 0.10 * (self.battery_soc_pct / 100.0))
            if power_kw is not None and power_kw != 0:
                battery_current = round((power_kw * 1000.0) / nominal_pack_v, 1)
            else:
                battery_current = 0.0
            voltage_sag = batt_comp.compute_voltage_sag(abs(battery_current))
            battery_voltage = round(nominal_pack_v - voltage_sag + float(rng_noise.normal(0.0, 0.2)), 1)
            battery_temp = round(ambient_c + abs(battery_current) * 0.05 + float(rng_noise.normal(0.0, 0.3)), 1)
        else:
            # ICE 12V system observable
            battery_level = 95.0
            battery_voltage = round(14.2 - (0.4 if k_data["speed_kmh"] < 1.0 else 0.0) + float(rng_noise.normal(0.0, 0.05)), 2)
            battery_current = round(2.0 + float(rng_noise.normal(0.0, 0.2)), 1)
            battery_temp = round(ambient_c + 5.0, 1)

        # 3. Component Degradation Steps & Maintenance Events
        all_new_events: List[MaintenanceEventRecord] = []

        # Brake step
        if brake_comp:
            brake_stress = {
                "harsh_brake_count": float(k_data["harsh_brake_count"]),
                "high_speed_decel_count": float(k_data["high_speed_decel_count"]),
            }
            evts = brake_comp.step(
                dt_hours=dt_hours if k_data["speed_kmh"] > 0 else 0.0,
                distance_km=k_data["distance_km"],
                stress_inputs=brake_stress,
                current_sim_time=current_sim_time,
                rng_degradation=rng_degradation,
                degradation_scale=degradation_scale * self.component_scale.get("BRAKE", 1.0),
            )
            self._tag_events(evts, "BRAKE")
            all_new_events.extend(evts)

        # Powertrain step
        if pt_comp:
            pt_stress = {}
            if self.vehicle_type == "ICE":
                pt_stress["high_temp_duration"] = dt_hours if (engine_temp_c and engine_temp_c > 105.0) else 0.0
                pt_stress["high_load_duration"] = dt_hours if (engine_load_pct and engine_load_pct > 80.0) else 0.0
                pt_stress["high_rpm_duration"] = dt_hours if (engine_rpm and engine_rpm > 4500.0) else 0.0
            else:
                pt_stress["high_temp_duration"] = dt_hours if (motor_temp_c and motor_temp_c > 90.0) else 0.0
                pt_stress["high_power_duration"] = dt_hours if (power_kw and power_kw > 60.0) else 0.0

            evts = pt_comp.step(
                dt_hours=dt_hours if k_data["speed_kmh"] > 0 else 0.0,
                distance_km=k_data["distance_km"],
                stress_inputs=pt_stress,
                current_sim_time=current_sim_time,
                rng_degradation=rng_degradation,
                degradation_scale=degradation_scale * self.component_scale.get("POWERTRAIN", 1.0),
            )
            self._tag_events(evts, "POWERTRAIN")
            all_new_events.extend(evts)

        # Battery step
        if batt_comp:
            batt_stress = {
                # The simulator only models Level 2 AC charging, so no DC fast-charge stress.
                "fast_charge_count": 0.0,
                "high_temp_duration": dt_hours if (battery_temp and battery_temp > 40.0) else 0.0,
                "deep_discharge_duration": dt_hours if self.battery_soc_pct < 10.0 else 0.0,
                "efc_fraction": (k_data["distance_km"] / 400.0),
            }
            # Battery degrades via calendar aging even when parked
            evts = batt_comp.step(
                dt_hours=dt_hours,
                distance_km=k_data["distance_km"],
                stress_inputs=batt_stress,
                current_sim_time=current_sim_time,
                rng_degradation=rng_degradation,
                degradation_scale=degradation_scale * self.component_scale.get("BATTERY", 1.0),
            )
            self._tag_events(evts, "BATTERY")
            all_new_events.extend(evts)

        # 4. DTC Resolution
        active_dtcs = self.fault_manager.get_active_dtcs(
            elapsed_seconds=elapsed_seconds,
            dt_seconds=dt_seconds,
            components=list(self.components.values()),
            rng_faults=rng_faults,
        )

        # Determine event string
        if k_data["is_harsh_brake"]:
            event_str = "HARSH_BRAKE"
        elif active_dtcs:
            event_str = "DTC"
        else:
            event_str = "TELEMETRY"

        # 5. Assemble Observable Telemetry (Strictly NO hidden fields!)
        telemetry_payload = {
            "vin": self.vin,
            "ts": current_sim_time.strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z",
            "lat": k_data["lat"],
            "lng": k_data["lng"],
            "vehicleSpeed": k_data["speed_kmh"],
            "mileageKm": round(self.odometer_km, 2),
            "batteryLevel": battery_level,
            "batteryVoltage": battery_voltage,
            "batteryCurrent": battery_current,
            "batteryTemp": battery_temp,
            "engineRpm": engine_rpm,
            "motorTemperature": motor_temp_c,
            "faultCodes": active_dtcs,
            "longitudinalAccel": k_data["longitudinal_accel"],
            "event": event_str,
            "sequence": self.seq,
            "engineTemp": engine_temp_c,
            "engineLoadPct": engine_load_pct,
            "powerKw": power_kw,
            "charging": self.is_charging if self.vehicle_type != "ICE" else None,
            "tripRef": current_trip_id,
        }

        return telemetry_payload, all_new_events, completed_trip

    def _tag_events(self, events: List[MaintenanceEventRecord], component: str):
        """Attach identity and the service-record odometer reading to new events."""
        for e in events:
            e.vehicle_id = self.vehicle_id
            e.vehicle_component_id = self.vehicle_component_ids[component]
            e.component = component
            e.odometer_km = round(self.odometer_km, 3)

    def get_state(self) -> dict:
        return {
            "vehicle_id": self.vehicle_id,
            "vin": self.vin,
            "seq": self.seq,
            "odometer_km": self.odometer_km,
            "battery_soc_pct": self.battery_soc_pct,
            "fuel_level_pct": self.fuel_level_pct,
            "is_charging": self.is_charging,
            "coolant_temp_c": self.coolant_temp_c,
            "motor_temp_c": self.motor_temp_c,
            "components": {k: c.get_state() for k, c in self.components.items()},
            "trip_scheduler": self.trip_scheduler.get_state(),
            "fault_manager": self.fault_manager.get_state(),
            "dynamics": self.dynamics.get_state(),
        }

    def set_state(self, state: dict):
        self.seq = state.get("seq", 0)
        self.odometer_km = state.get("odometer_km", self.odometer_km)
        self.battery_soc_pct = state.get("battery_soc_pct", self.battery_soc_pct)
        self.fuel_level_pct = state.get("fuel_level_pct", self.fuel_level_pct)
        self.is_charging = state.get("is_charging", False)
        self.coolant_temp_c = state.get("coolant_temp_c", 85.0)
        self.motor_temp_c = state.get("motor_temp_c", 45.0)
        for k, c_state in state.get("components", {}).items():
            if k in self.components:
                self.components[k].set_state(c_state)
        if "trip_scheduler" in state:
            self.trip_scheduler.set_state(state["trip_scheduler"])
        if "fault_manager" in state:
            self.fault_manager.set_state(state["fault_manager"])
        if "dynamics" in state:
            self.dynamics.set_state(state["dynamics"])


class FleetPopulation:
    @staticmethod
    def generate_population(
        config,
        rng_population,
        legacy_mode: bool = False,
    ) -> List[SimVehicle]:
        """
        Instantiates fleet of SimVehicle objects based on configuration and proportions.
        """
        num_vehicles = config.population.vehicles
        num_tenants = config.population.tenants
        num_fleets_per_tenant = config.population.fleets_per_tenant

        # Tenant and fleet IDs
        tenant_ids = [
            str(uuid.uuid5(uuid.NAMESPACE_DNS, f"tenant-{t_idx}"))
            for t_idx in range(num_tenants)
        ]
        # In legacy mode or demo mode, ensure first tenant matches init.sql
        tenant_ids[0] = "00000000-0000-0000-0000-000000000001"

        fleet_ids = []
        for t_id in tenant_ids:
            for f_idx in range(num_fleets_per_tenant):
                f_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{t_id}:fleet-{f_idx}"))
                fleet_ids.append(f_id)
        fleet_ids[0] = "00000000-0000-0000-0000-000000000002"

        vehicles: List[SimVehicle] = []

        # Population proportions
        v_types = ["ICE", "EV", "HYBRID"]
        p_types = [
            config.population.vehicle_types.get("ICE", 0.50),
            config.population.vehicle_types.get("EV", 0.30),
            config.population.vehicle_types.get("HYBRID", 0.20),
        ]
        # Normalize probabilities
        sum_p = sum(p_types)
        p_types = [p / sum_p for p in p_types]

        driver_behaviors = [
            DriverBehavior.CONSERVATIVE,
            DriverBehavior.NORMAL,
            DriverBehavior.AGGRESSIVE,
        ]
        p_behaviors = [0.40, 0.45, 0.15]

        driving_profiles = [
            DrivingProfile.CITY,
            DrivingProfile.HIGHWAY,
            DrivingProfile.MIXED,
        ]
        p_profiles = [
            config.population.driving_profiles.get("CITY", 0.35),
            config.population.driving_profiles.get("HIGHWAY", 0.25),
            config.population.driving_profiles.get("MIXED", 0.40),
        ]
        sum_prof = sum(p_profiles)
        p_profiles = [p / sum_prof for p in p_profiles]

        used_vins = set()

        for idx in range(num_vehicles):
            fleet_idx = idx % len(fleet_ids)
            fleet_id = fleet_ids[fleet_idx]
            # A vehicle belongs to its fleet's tenant (fleets are generated per tenant above).
            tenant_id = tenant_ids[fleet_idx // num_fleets_per_tenant]

            if idx == 0 and (legacy_mode or config.population.vin):
                # Preserved legacy vehicle
                vin = config.population.vin or "1HGCM82633A004352"
                vehicle_id = "00000000-0000-0000-0000-000000000003"
                v_type = "EV"
                driver_behavior = DriverBehavior.NORMAL
                driving_profile = DrivingProfile.CITY
                age_years = 2.0
                odometer_km = 12450.0
                wear_mult = 1.0
                home_lat, home_lng = 37.774929, -122.419416
                make, model, year = "Tesla", "Model 3", 2022
            else:
                # Stochastic fleet synthesis
                v_type = v_types[int(rng_population.choice(len(v_types), p=p_types))]
                driver_behavior = driver_behaviors[int(rng_population.choice(len(driver_behaviors), p=p_behaviors))]
                driving_profile = driving_profiles[int(rng_population.choice(len(driving_profiles), p=p_profiles))]

                # VIN generation
                wmi = "1HG" if v_type == "ICE" else ("5YJ" if v_type == "EV" else "4T1")
                serial_idx = config.population.vin_serial_start + idx
                vin = generate_vin(rng_population, serial=serial_idx, wmi=wmi)
                while vin in used_vins:
                    serial_idx += 500
                    vin = generate_vin(rng_population, serial=serial_idx, wmi=wmi)
                used_vins.add(vin)

                vehicle_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"vehicle:{vin}"))

                # Age and odometer
                # Lognormal with median ~2.5 years, bounded [0.1, 8.0]
                age_raw = float(rng_population.lognormal(mean=0.916, sigma=0.45))
                age_years = round(max(0.1, min(8.0, age_raw)), 2)
                km_per_year = float(rng_population.uniform(15000.0, 35000.0))
                odometer_km = round(age_years * km_per_year, 1)

                # Wear multiplier: lognormal(0, sigma from config)
                wear_mult = float(rng_population.lognormal(mean=0.0, sigma=config.degradation.wear_rate_lognormal_sigma))

                # Home depot coordinates
                depot = DEPOT_COORDINATES[idx % len(DEPOT_COORDINATES)]
                home_lat = depot[0] + float(rng_population.uniform(-0.05, 0.05))
                home_lng = depot[1] + float(rng_population.uniform(-0.05, 0.05))

                catalog_entry = rng_population.choice(VEHICLE_CATALOG[v_type])
                make, model = catalog_entry[0], catalog_entry[1]
                year = 2024 - int(math.floor(age_years))

            # Driver ID
            driver_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"driver:{vehicle_id}"))

            # Initial wear per component. A fleet is observed mid-service-cycle, so the
            # wear since the last service is spread uniformly over the configured range
            # (no vehicle starts at or beyond the maintenance threshold).
            deg = config.degradation
            def sample_initial_wear():
                return float(rng_population.uniform(deg.initial_wear_min, deg.initial_wear_max))

            init_brake_wear = sample_initial_wear()
            init_pt_wear = sample_initial_wear()
            init_batt_wear = sample_initial_wear() if v_type in ("EV", "HYBRID") else 0.0

            # Component IDs
            if vehicle_id == "00000000-0000-0000-0000-000000000003":
                comp_ids = {
                    "BRAKE": "00000000-0000-0000-0000-0000000000a1",
                    "POWERTRAIN": "00000000-0000-0000-0000-0000000000a2",
                    "BATTERY": "00000000-0000-0000-0000-0000000000a3",
                }
            else:
                comp_ids = {
                    "BRAKE": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{vehicle_id}:BRAKE")),
                    "POWERTRAIN": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{vehicle_id}:POWERTRAIN")),
                    "BATTERY": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{vehicle_id}:BATTERY")),
                }

            components = build_components(config, v_type, init_brake_wear, init_pt_wear, init_batt_wear, wear_mult)

            vehicle = SimVehicle(
                vehicle_id=vehicle_id,
                fleet_id=fleet_id,
                tenant_id=tenant_id,
                driver_id=driver_id,
                vin=vin,
                oem_id=assign_oem(vin, config),
                vehicle_type=v_type,
                make=make,
                model=model,
                year=year,
                driver_behavior=driver_behavior,
                driving_profile=driving_profile,
                age_years=age_years,
                initial_odometer_km=odometer_km,
                wear_rate_multiplier=wear_mult,
                home_lat=home_lat,
                home_lng=home_lng,
                components=components,
                vehicle_component_ids=comp_ids,
                seq=0,
            )
            vehicle.component_scale = dict(config.degradation.component_scale)
            vehicles.append(vehicle)

        return vehicles
