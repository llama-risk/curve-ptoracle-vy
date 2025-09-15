# pragma version 0.4.3
# pragma optimize gas
# pragma nonreentrancy on
# @license BUSL-1.1

from ethereum.ercs import IERC165

implements: IERC165

from pcaversaccio.snekmate.src.snekmate.auth.interfaces import IAccessControl

implements: IAccessControl

from pcaversaccio.snekmate.src.snekmate.auth import access_control

initializes: access_control

exports: (access_control.IERC165, access_control.IAccessControl)

# Interface for Principal Token example pt token: 0x6d98a2b6cdbf44939362a3e99793339ba2016af4
interface IPendlePT:
    def expiry() -> uint256: view


# Interface for underlying oracle
interface IOracle:
    def price() -> uint256: view


# Constants
DISCOUNT_PRECISION: constant(uint256) = 10**18
SECONDS_PER_YEAR: constant(uint256) = 365 * 24 * 60 * 60  # 31,536,000 seconds

# Role constants
MANAGER_ROLE: public(constant(bytes32)) = keccak256("MANAGER_ROLE")
ADMIN_ROLE: public(constant(bytes32)) = keccak256("ADMIN_ROLE")

# State variables
pt: public(IPendlePT)
underlying_oracle: public(IOracle)
slope: public(uint256)  # Linear discount slope with 1e18 precision
intercept: public(uint256)  # Linear discount intercept with 1e18 precision
max_update_interval: public(uint256)
pt_expiry: public(immutable(uint256))

# Limit variables for slope and intercept changes (0 = no limit)
max_slope_change: public(uint256)  # Maximum allowed change in slope per update
max_intercept_change: public(
    uint256
)  # Maximum allowed change in intercept per update

# Price storage
last_update: public(uint256)
last_price: public(uint256)

# Rate limiting for set_linear_discount
last_discount_update: public(uint256)

# Events
event LinearDiscountUpdated:
    new_slope: indexed(uint256)
    new_intercept: indexed(uint256)


event LimitsUpdated:
    new_max_update_interval: uint256
    new_max_slope_change: uint256
    new_max_intercept_change: uint256


event PriceUpdated:
    new_price: indexed(uint256)


event OracleInitialized:
    pt: indexed(IPendlePT)
    underlying_oracle: indexed(IOracle)
    initial_slope: uint256
    initial_intercept: uint256


# Constructor
@deploy
def __init__(
    _pt: IPendlePT,
    _underlying_oracle: IOracle,
    _slope: uint256,
    _intercept: uint256,
    _max_update_interval: uint256,
    _manager: address,
    _admin: address,
):
    # Validate all addresses are non-zero
    assert _pt != empty(IPendlePT), "invalid PT address"
    assert _underlying_oracle != empty(IOracle), "invalid oracle address"
    assert _manager != empty(address), "invalid manager address"
    assert _admin != empty(address), "invalid admin address"

    # Validate initial parameters
    assert _slope <= DISCOUNT_PRECISION, "initial slope exceeds precision"
    assert (
        _intercept <= DISCOUNT_PRECISION
    ), "initial intercept exceeds precision"
    assert _max_update_interval > 0, "invalid update interval"

    # Initialize access control (msg.sender becomes default admin)
    access_control.__init__()

    # Set ADMIN_ROLE as the admin for MANAGER_ROLE
    access_control._set_role_admin(MANAGER_ROLE, ADMIN_ROLE)

    # Grant roles
    access_control._grant_role(MANAGER_ROLE, _manager)
    access_control._grant_role(ADMIN_ROLE, _admin)

    # Revoke default admin role from deployer
    access_control._revoke_role(access_control.DEFAULT_ADMIN_ROLE, msg.sender)

    self.pt = _pt
    self.underlying_oracle = _underlying_oracle
    self.slope = _slope
    self.intercept = _intercept
    self.max_update_interval = _max_update_interval
    self.max_slope_change = 0  # Initialize to 0 (no limit)
    self.max_intercept_change = 0  # Initialize to 0 (no limit)
    self.last_update = block.timestamp
    self.last_discount_update = (
        block.timestamp
    )  # Initialize discount update timestamp
    pt_expiry = staticcall _pt.expiry()

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
    underlying_price: uint256 = staticcall self.underlying_oracle.price()

    if pt_expiry <= block.timestamp:
        return underlying_price

    time_to_maturity_seconds: uint256 = pt_expiry - block.timestamp

    # Convert time to maturity to years with 1e18 precision
    time_to_maturity_years: uint256 = (
        time_to_maturity_seconds * DISCOUNT_PRECISION
    ) // SECONDS_PER_YEAR

    # Linear discount with 1e18 precision: discount = (slope * time_to_maturity_years) / 1e18 + intercept
    discount: uint256 = (
        self.slope * time_to_maturity_years
    ) // DISCOUNT_PRECISION + self.intercept

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
        return staticcall self.underlying_oracle.price()

    if block.timestamp == self.last_update:
        return self.last_price

    new_price: uint256 = self._calculate_price()

    # Update price and timestamp
    self.last_price = new_price
    self.last_update = block.timestamp

    # Emit price update event
    log PriceUpdated(new_price=new_price)

    return new_price


# Internal function to update discount parameters
@internal
def _update_discount_params(_slope: uint256, _intercept: uint256):
    # Validate slope and intercept bounds to prevent extreme discounts
    assert _slope <= DISCOUNT_PRECISION, "slope exceeds precision"
    assert _intercept <= DISCOUNT_PRECISION, "intercept exceeds precision"

    # Check slope change limit (0 means no limit)
    if self.max_slope_change > 0:
        slope_change: uint256 = 0
        if _slope > self.slope:
            slope_change = _slope - self.slope
        else:
            slope_change = self.slope - _slope
        assert (
            slope_change <= self.max_slope_change
        ), "slope change exceeds limit"

    if self.max_intercept_change > 0:
        intercept_change: uint256 = 0
        if _intercept > self.intercept:
            intercept_change = _intercept - self.intercept
        else:
            intercept_change = self.intercept - _intercept
        assert (
            intercept_change <= self.max_intercept_change
        ), "intercept change exceeds limit"

    time_to_maturity_seconds: uint256 = pt_expiry - block.timestamp

    # Convert time to maturity to years with 1e18 precision
    time_to_maturity_years: uint256 = (
        time_to_maturity_seconds * DISCOUNT_PRECISION
    ) // SECONDS_PER_YEAR

    # Linear discount with 1e18 precision: discount = (slope * time_to_maturity_years) / 1e18 + intercept
    discount: uint256 = (
        self.slope * time_to_maturity_years
    ) // DISCOUNT_PRECISION + self.intercept
    assert discount <= DISCOUNT_PRECISION, "discount exceeds precision"

    self.slope = _slope
    self.intercept = _intercept
    self.last_discount_update = block.timestamp

    log LinearDiscountUpdated(
        new_slope=_slope,
        new_intercept=_intercept,
    )


# Write functions - Manager only
@external
@nonpayable
def set_linear_discount(_slope: uint256, _intercept: uint256):
    access_control._check_role(MANAGER_ROLE, msg.sender)

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
    access_control._check_role(MANAGER_ROLE, msg.sender)

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
def set_limits(
    _max_update_interval: uint256,
    _max_slope_change: uint256,
    _max_intercept_change: uint256,
):
    access_control._check_role(ADMIN_ROLE, msg.sender)

    self.max_update_interval = _max_update_interval
    self.max_slope_change = _max_slope_change
    self.max_intercept_change = _max_intercept_change

    log LimitsUpdated(
        new_max_update_interval=_max_update_interval,
        new_max_slope_change=_max_slope_change,
        new_max_intercept_change=_max_intercept_change,
    )
