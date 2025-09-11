# pragma version 0.4.3
# pragma optimize gas
# @license MIT
# Security: Nonreentrancy is enabled by default in Vyper 0.4.3 for all external functions

# Interface for Principal Token example pt token: 0x6d98a2b6cdbf44939362a3e99793339ba2016af4
interface PendlePT:
    def expiry() -> uint256: view


# Interface for underlying oracle
interface Oracle:
    def price() -> uint256: view


# Constants
DISCOUNT_PRECISION: constant(uint256) = 10**18

# State variables
pt: public(address)
underlying_oracle: public(address)
slope: public(uint256)  # Linear discount slope with 1e18 precision
intercept: public(uint256)  # Linear discount intercept with 1e18 precision
max_update_interval: public(uint256)
pt_expiry: public(immutable(uint256))

# Limit variables for slope and intercept changes (0 = no limit)
max_slope_change: public(uint256)  # Maximum allowed change in slope per update
max_intercept_change: public(
    uint256
)  # Maximum allowed change in intercept per update

# Access control
manager: public(address)
admin: public(address)

# Modifiers
@internal
def _only_manager():
    assert msg.sender == self.manager, "caller is not manager"


@internal
def _only_admin():
    assert msg.sender == self.admin, "caller is not admin"


# Price storage
last_update: public(uint256)
last_price: public(uint256)

# Rate limiting for set_linear_discount
last_discount_update: public(uint256)

# Events
event LinearDiscountUpdated:
    old_slope: indexed(uint256)
    old_intercept: indexed(uint256)
    new_slope: uint256
    new_intercept: uint256


event LimitsUpdated:
    old_max_update_interval: uint256
    new_max_update_interval: uint256
    old_max_slope_change: uint256
    new_max_slope_change: uint256
    old_max_intercept_change: uint256
    new_max_intercept_change: uint256


event PriceUpdated:
    old_price: indexed(uint256)
    new_price: indexed(uint256)
    timestamp: uint256


event OracleInitialized:
    pt: indexed(address)
    underlying_oracle: indexed(address)
    initial_slope: uint256
    initial_intercept: uint256


event ManagerUpdated:
    old_manager: indexed(address)
    new_manager: indexed(address)


# Constructor
@deploy
def __init__(
    _pt: address,
    _underlying_oracle: address,
    _slope: uint256,
    _intercept: uint256,
    _max_update_interval: uint256,
    _manager: address,
    _admin: address,
):
    # Validate all addresses are non-zero
    assert _pt != empty(address), "invalid PT address"
    assert _underlying_oracle != empty(address), "invalid oracle address"
    assert _manager != empty(address), "invalid manager address"
    assert _admin != empty(address), "invalid admin address"

    # Validate initial parameters
    assert _slope <= DISCOUNT_PRECISION, "initial slope exceeds precision"
    assert (
        _intercept <= DISCOUNT_PRECISION
    ), "initial intercept exceeds precision"
    assert _max_update_interval > 0, "invalid update interval"

    self.pt = _pt
    self.underlying_oracle = _underlying_oracle
    self.slope = _slope
    self.intercept = _intercept
    self.max_update_interval = _max_update_interval
    self.max_slope_change = 0  # Initialize to 0 (no limit)
    self.max_intercept_change = 0  # Initialize to 0 (no limit)
    self.manager = _manager
    self.admin = _admin
    self.last_update = block.timestamp
    self.last_discount_update = (
        block.timestamp
    )  # Initialize discount update timestamp
    pt_expiry = staticcall PendlePT(_pt).expiry()

    # Initialize price
    self.last_price = self._calculate_price()

    # Emit initialization event
    log OracleInitialized(
        pt=_pt,
        underlying_oracle=_underlying_oracle,
        initial_slope=_slope,
        initial_intercept=_intercept,
    )


# Internal functions
@internal
@view
def _calculate_price() -> uint256:

    # Get underlying oracle price and apply discount
    underlying_price: uint256 = staticcall Oracle(
        self.underlying_oracle
    ).price()

    if pt_expiry <= block.timestamp:
        return underlying_price

    time_to_maturity_seconds: uint256 = pt_expiry - block.timestamp

    # Convert time to maturity to years with 1e18 precision
    # 1 year = 365 * 24 * 60 * 60 = 31,536,000 seconds
    SECONDS_PER_YEAR: uint256 = 365 * 24 * 60 * 60
    time_to_maturity_years: uint256 = (
        time_to_maturity_seconds * DISCOUNT_PRECISION
    ) // SECONDS_PER_YEAR

    # Linear discount with 1e18 precision: discount = (slope * time_to_maturity_years) / 1e18 + intercept
    discount: uint256 = (
        self.slope * time_to_maturity_years
    ) // DISCOUNT_PRECISION + self.intercept

    # Ensure discount doesn't exceed precision (should never happen but critical for safety)
    assert discount <= DISCOUNT_PRECISION, "discount exceeds precision"

    # Calculate discount factor (between 0 and 1 with 1e18 precision)
    discount_factor: uint256 = DISCOUNT_PRECISION - discount

    # Return discounted price: underlying_price * discount_factor / DISCOUNT_PRECISION
    return (underlying_price * discount_factor) // DISCOUNT_PRECISION


# View functions
@view
@external
def price() -> uint256:
    return self._calculate_price()


# Write functions - All callers
@external
@nonpayable
def price_w() -> uint256:
    # If PT has expired, return underlying oracle price
    if pt_expiry <= block.timestamp:
        return staticcall Oracle(self.underlying_oracle).price()

    if block.timestamp == self.last_update:
        return self.last_price

    new_price: uint256 = self._calculate_price()

    # Store old price for event
    old_price: uint256 = self.last_price

    # Update price and timestamp
    self.last_price = new_price
    self.last_update = block.timestamp

    # Emit price update event
    log PriceUpdated(
        old_price=old_price, new_price=new_price, timestamp=block.timestamp
    )

    return new_price


# Internal function to update discount parameters
@internal
def _update_discount_params(_slope: uint256, _intercept: uint256):
    # Validate slope and intercept bounds to prevent extreme discounts
    assert _slope <= DISCOUNT_PRECISION, "slope exceeds precision"
    assert _intercept <= DISCOUNT_PRECISION, "intercept exceeds precision"

    # Store old values for event and limit checks
    old_slope: uint256 = self.slope
    old_intercept: uint256 = self.intercept

    # Check slope change limit (0 means no limit)
    if self.max_slope_change > 0:
        slope_change: uint256 = 0
        if _slope > old_slope:
            slope_change = _slope - old_slope
        else:
            slope_change = old_slope - _slope
        assert (
            slope_change <= self.max_slope_change
        ), "slope change exceeds limit"


    # Check intercept change limit (0 means no limit)
    if self.max_intercept_change > 0:
        intercept_change: uint256 = 0
        if _intercept > old_intercept:
            intercept_change = _intercept - old_intercept
        else:
            intercept_change = old_intercept - _intercept
        assert (
            intercept_change <= self.max_intercept_change
        ), "intercept change exceeds limit"


    # Update parameters
    self.slope = _slope
    self.intercept = _intercept
    self.last_discount_update = block.timestamp

    log LinearDiscountUpdated(
        old_slope=old_slope,
        old_intercept=old_intercept,
        new_slope=_slope,
        new_intercept=_intercept,
    )


# Write functions - Manager only
@external
@nonpayable
def set_linear_discount(_slope: uint256, _intercept: uint256):
    self._only_manager()

    # Check rate limiting - Manager can only update once per max_update_interval
    assert (
        block.timestamp > self.last_discount_update + self.max_update_interval
    ), "update interval not elapsed"

    self._update_discount_params(_slope, _intercept)


@external
@nonpayable
def set_slope_from_apy(expected_apy: uint256):
    """
    @notice Set slope based on expected APY, with intercept set to 0
    @param expected_apy Expected APY with 1e18 precision (e.g., 5% = 5e16)
    """
    self._only_manager()

    # Check rate limiting - Manager can only update once per max_update_interval
    assert (
        block.timestamp > self.last_discount_update + self.max_update_interval
    ), "update interval not elapsed"

    # Validate APY is within reasonable bounds (e.g., max 1000% APY)
    assert expected_apy <= 10 * DISCOUNT_PRECISION, "APY exceeds maximum bound"
    assert expected_apy > 0, "APY must be positive"

    # For linear discount model with yearly slope:
    # If we want an APY of X%, the slope should be X% per year
    # Since time_to_maturity is now in years with 1e18 precision,
    # and discount = (slope * time_to_maturity_years) / 1e18
    # We want: discount_per_year = expected_apy / (1 + expected_apy)

    # Calculate the discount rate from APY
    # discount_rate = APY / (1 + APY)
    numerator: uint256 = expected_apy * DISCOUNT_PRECISION
    denominator: uint256 = DISCOUNT_PRECISION + expected_apy

    # Ensure denominator won't cause division by zero
    assert denominator > 0, "invalid denominator"

    # Calculate slope (discount per year with 1e18 precision)
    new_slope: uint256 = numerator // denominator

    self._update_discount_params(new_slope, 0)


# Write functions - Admin only
@external
@nonpayable
def set_manager(_new_manager: address) -> bool:
    """
    @notice Update the manager address (admin-only function)
    @param _new_manager The new manager address
    @return True on successful update
    """
    self._only_admin()

    # Validate new manager address is not zero
    assert _new_manager != empty(address), "invalid manager address"

    # Store old manager for event
    old_manager: address = self.manager

    # Update manager
    self.manager = _new_manager

    # Emit event
    log ManagerUpdated(old_manager=old_manager, new_manager=_new_manager)

    return True


@external
@nonpayable
def set_limits(
    _max_update_interval: uint256,
    _max_slope_change: uint256,
    _max_intercept_change: uint256,
):
    self._only_admin()
    # Store old values for event
    old_max_update_interval: uint256 = self.max_update_interval
    old_max_slope_change: uint256 = self.max_slope_change
    old_max_intercept_change: uint256 = self.max_intercept_change

    self.max_update_interval = _max_update_interval
    self.max_slope_change = _max_slope_change
    self.max_intercept_change = _max_intercept_change

    log LimitsUpdated(
        old_max_update_interval=old_max_update_interval,
        new_max_update_interval=_max_update_interval,
        old_max_slope_change=old_max_slope_change,
        new_max_slope_change=_max_slope_change,
        old_max_intercept_change=old_max_intercept_change,
        new_max_intercept_change=_max_intercept_change,
    )
