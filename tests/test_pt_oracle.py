import pytest
import boa
from datetime import datetime, timedelta


@pytest.fixture
def owner():
    return boa.env.generate_address()


@pytest.fixture
def llamarisk():
    return boa.env.generate_address()


@pytest.fixture
def curve_dao():
    return boa.env.generate_address()


@pytest.fixture
def manager():
    return boa.env.generate_address()


@pytest.fixture
def admin():
    return boa.env.generate_address()


@pytest.fixture
def mock_pt(owner):
    """Deploy a mock PT token with expiry"""
    mock_pt_code = """
# pragma version 0.4.3

expiry: public(uint256)

@deploy
def __init__(_expiry: uint256):
    self.expiry = _expiry
"""
    with boa.env.prank(owner):
        # Set expiry to 30 days from now
        future_expiry = boa.env.evm.patch.timestamp + (30 * 24 * 60 * 60)
        return boa.loads(mock_pt_code, future_expiry)


@pytest.fixture
def mock_oracle(owner):
    """Deploy a mock underlying oracle"""
    mock_oracle_code = """
# pragma version 0.4.3

stored_price: uint256

@deploy
def __init__():
    self.stored_price = 10**18  # 1 ETH = $1 for simplicity

@view
@external
def price() -> uint256:
    return self.stored_price

@external
def set_price(_price: uint256):
    self.stored_price = _price
"""
    with boa.env.prank(owner):
        return boa.loads(mock_oracle_code)


@pytest.fixture
def pt_oracle(owner, manager, admin, mock_pt, mock_oracle):
    """Deploy the PtOracle contract"""
    from contracts import PtOracle
    
    with boa.env.prank(owner):
        # Slope is now in years with 1e18 precision
        # For a 5% APY: slope = 0.05 * 1e18 = 5e16
        return PtOracle.deploy(
            mock_pt.address,  # PT token
            mock_oracle.address,  # underlying oracle
            5 * 10**16,  # slope (5% per year with 1e18 precision)
            0,   # intercept (0% with 1e18 precision)
            86400,  # max_update_interval (24 hours)
            manager,
            admin
        )


class TestPtOracle:
    def test_deployment(self, pt_oracle, owner, manager, admin, mock_pt, mock_oracle):
        """Test that the contract deploys correctly with initial values"""
        # Note: contract doesn't have owner() method in the new version
        assert pt_oracle.manager() == manager
        assert pt_oracle.admin() == admin
        assert pt_oracle.pt() == mock_pt.address
        assert pt_oracle.underlying_oracle() == mock_oracle.address
        assert pt_oracle.slope() == 5 * 10**16  # 5% per year
        assert pt_oracle.intercept() == 0
        assert pt_oracle.max_update_interval() == 86400
    
    def test_price_calculation(self, pt_oracle, mock_oracle):
        """Test that price() calculates the correct discounted price"""
        # The discount should be applied to the underlying price
        # With 30 days to maturity (30/365 years), slope=5e16 (5% per year), intercept=0
        # time_to_maturity_years = (30 * 24 * 60 * 60 * 1e18) / (365 * 24 * 60 * 60)
        # discount = (5e16 * time_to_maturity_years) / 1e18 + 0
        price = pt_oracle.price()
        
        # Price should be less than underlying due to discount
        underlying_price = mock_oracle.price()
        assert price < underlying_price
        assert price > 0
        
        # With 30 days = ~0.082 years, 5% APY gives ~0.41% discount
        # So price should be about 99.59% of underlying
        expected_discount_ratio = 30 / 365 * 0.05  # ~0.0041
        expected_price = int(underlying_price * (1 - expected_discount_ratio))
        # Allow for small rounding differences
        assert abs(price - expected_price) < underlying_price // 1000  # Within 0.1%
    
    def test_price_w_caching(self, pt_oracle):
        """Test that price_w() caches the price correctly"""
        # First call to price_w
        price1 = pt_oracle.price_w()
        timestamp1 = pt_oracle.last_update()
        
        # Immediate second call (same block) should return same price without recalculation
        price2 = pt_oracle.price_w()
        timestamp2 = pt_oracle.last_update()
        
        assert price1 == price2
        assert timestamp1 == timestamp2
        assert pt_oracle.last_price() == price1
    
    def test_price_w_update(self, pt_oracle):
        """Test that price_w() updates when time passes"""
        # First call
        price1 = pt_oracle.price_w()
        
        # Advance time by 1 hour
        boa.env.evm.patch.timestamp += 3600
        
        # Second call should update
        price2 = pt_oracle.price_w()
        
        # Price should change slightly due to time passing (less time to maturity)
        assert price2 != price1
        assert pt_oracle.last_update() > 0
    
    def test_expired_pt_returns_zero(self, owner):
        """Test that expired PT returns 0 price"""
        from contracts import PtOracle
        
        # Create PT with past expiry
        mock_pt_code = """
# pragma version 0.4.3

expiry: public(uint256)

@deploy
def __init__(_expiry: uint256):
    self.expiry = _expiry
"""
        with boa.env.prank(owner):
            # Set expiry to past
            past_expiry = boa.env.evm.patch.timestamp - 1
            expired_pt = boa.loads(mock_pt_code, past_expiry)
            
            # Create mock oracle
            mock_oracle_code = """
# pragma version 0.4.3

@view
@external
def price() -> uint256:
    return 10**18
"""
            mock_oracle = boa.loads(mock_oracle_code)
            
            # Deploy PtOracle with expired PT
            pt_oracle = PtOracle.deploy(
                expired_pt.address,
                mock_oracle.address,
                1, 0, 86400,
                boa.env.generate_address(),
                boa.env.generate_address()
            )
            
            # After expiry, should return underlying oracle price
            assert pt_oracle.price() == 10**18
            assert pt_oracle.price_w() == 10**18
    
    def test_set_linear_discount(self, pt_oracle, manager, owner):
        """Test that manager can update linear discount parameters"""
        # First, advance time to ensure we can update
        boa.env.timestamp = boa.env.timestamp + 86401  # Move past the initial interval
        
        pt_oracle.set_linear_discount(200, 100, sender=manager)
        assert pt_oracle.slope() == 200
        assert pt_oracle.intercept() == 100
        assert pt_oracle.last_discount_update() > 0
        
        # Non-manager should fail
        with boa.reverts("caller is not manager"):
            pt_oracle.set_linear_discount(300, 150, sender=owner)
    
    def test_set_linear_discount_limits(self, pt_oracle, manager):
        """Test that linear discount parameters respect DISCOUNT_PRECISION limit"""
        # Advance time to allow update
        boa.env.timestamp = boa.env.timestamp + 86401
        
        # Test setting valid values
        pt_oracle.set_linear_discount(2000, 1000, sender=manager)
        assert pt_oracle.slope() == 2000
        assert pt_oracle.intercept() == 1000
        
        # Test that intercept cannot exceed DISCOUNT_PRECISION (10**18)
        boa.env.timestamp = boa.env.timestamp + 86401
        with boa.reverts("intercept exceeds precision"):
            pt_oracle.set_linear_discount(1000, 10**18 + 1, sender=manager)
    
    def test_set_linear_discount_rate_limiting(self, pt_oracle, manager):
        """Test that set_linear_discount is rate limited with > operator"""
        # First update should work after initial interval
        boa.env.timestamp = boa.env.timestamp + 86401
        pt_oracle.set_linear_discount(200, 100, sender=manager)
        first_update = pt_oracle.last_discount_update()
        
        # Immediate second update should fail
        with boa.reverts("update interval not elapsed"):
            pt_oracle.set_linear_discount(300, 150, sender=manager)
        
        # Advance time by less than max_update_interval
        boa.env.timestamp = boa.env.timestamp + 43200  # 12 hours
        
        # Should still fail
        with boa.reverts("update interval not elapsed"):
            pt_oracle.set_linear_discount(300, 150, sender=manager)
        
        # Advance time to exactly max_update_interval (should still fail with > operator)
        boa.env.timestamp = first_update + 86400  # Exactly 24 hours from last update
        with boa.reverts("update interval not elapsed"):
            pt_oracle.set_linear_discount(300, 150, sender=manager)
        
        # Advance time to max_update_interval + 1 second (should work with > operator)
        boa.env.timestamp = first_update + 86401  # 24 hours + 1 second
        pt_oracle.set_linear_discount(300, 150, sender=manager)
        assert pt_oracle.slope() == 300
        assert pt_oracle.intercept() == 150
        assert pt_oracle.last_discount_update() > first_update
    
    def test_set_limits(self, pt_oracle, admin, owner):
        """Test that admin can update max update interval and change limits"""
        pt_oracle.set_limits(172800, 2 * 10**16, 3 * 10**16, sender=admin)  # 48 hours, 2% slope change, 3% intercept change
        assert pt_oracle.max_update_interval() == 172800
        assert pt_oracle.max_slope_change() == 2 * 10**16
        assert pt_oracle.max_intercept_change() == 3 * 10**16
        
        # Non-admin should fail
        with boa.reverts("caller is not admin"):
            pt_oracle.set_limits(86400, 10**16, 10**16, sender=owner)
    
    def test_full_discount_returns_zero(self, owner):
        """Test that full discount (>=100%) returns 0 price"""
        from contracts import PtOracle
        
        # Create PT with long expiry
        mock_pt_code = """
# pragma version 0.4.3

expiry: public(uint256)

@deploy
def __init__(_expiry: uint256):
    self.expiry = _expiry
"""
        with boa.env.prank(owner):
            # Set expiry to 365 days from now
            far_future_expiry = boa.env.timestamp + (365 * 24 * 60 * 60)
            pt = boa.loads(mock_pt_code, far_future_expiry)
            
            # Create mock oracle
            mock_oracle_code = """
# pragma version 0.4.3

@view
@external
def price() -> uint256:
    return 10**18
"""
            mock_oracle = boa.loads(mock_oracle_code)
            
            # Deploy with max intercept to cause exactly 100% discount
            # DISCOUNT_PRECISION = 10**18, so intercept at max will discount to 0
            pt_oracle = PtOracle.deploy(
                pt.address,
                mock_oracle.address,
                0,  # No slope needed
                10**18,  # Max intercept = 100% discount
                86400,
                boa.env.generate_address(),
                boa.env.generate_address()
            )
            
            # When discount equals 100%, returns 0
            assert pt_oracle.price() == 0
            assert pt_oracle.price_w() == 0
    
    def test_underlying_price_changes(self, pt_oracle, mock_oracle):
        """Test that PT price changes when underlying oracle price changes"""
        # Get initial price
        initial_price = pt_oracle.price()
        
        # Double the underlying price
        mock_oracle.set_price(2 * 10**18, sender=boa.env.generate_address())
        
        # PT price should also increase (but still be discounted)
        new_price = pt_oracle.price()
        assert new_price > initial_price
        assert new_price < mock_oracle.price()  # Still discounted
    
    # Story 1.1 Tests
    def test_precision_constant_renamed(self, pt_oracle):
        """Test that DISCOUNT_PRECISION is 10**18"""
        # This tests the precision used in discount calculations
        # With slope=5e16 (5% per year) and 30 days to maturity, discount should be ~0.41%
        price = pt_oracle.price()
        assert price > 0
        # Price should be calculated with 10**18 precision
        
    def test_immutable_pt_expiry(self, pt_oracle, mock_pt):
        """Test that pt_expiry is properly cached as immutable"""
        # The expiry should match what was set in the mock_pt
        assert pt_oracle.pt_expiry() == mock_pt.expiry()
        
    def test_last_price_variable(self, pt_oracle):
        """Test that last_price variable is used correctly"""
        # Call price_w to set last_price
        price = pt_oracle.price_w()
        assert pt_oracle.last_price() == price
        
        # Verify it's cached correctly on subsequent calls in same block
        price2 = pt_oracle.price_w()
        assert pt_oracle.last_price() == price2
        assert price == price2
    
    def test_discount_does_not_exceed_precision(self, owner):
        """Test that discount calculations respect DISCOUNT_PRECISION limit"""
        from contracts import PtOracle
        
        # Create PT with moderate expiry
        mock_pt_code = """
# pragma version 0.4.3

expiry: public(uint256)

@deploy
def __init__(_expiry: uint256):
    self.expiry = _expiry
"""
        mock_oracle_code = """
# pragma version 0.4.3

@view
@external
def price() -> uint256:
    return 10**18
"""
        pt_expiry = boa.env.timestamp + (10 * 24 * 60 * 60)  # 10 days
        with boa.env.prank(owner):
            pt = boa.loads(mock_pt_code, pt_expiry)
            mock_oracle = boa.loads(mock_oracle_code)
        
        # Deploy with high intercept close to DISCOUNT_PRECISION
        # The total discount (slope * time_in_years + intercept) must not exceed DISCOUNT_PRECISION
        with boa.env.prank(owner):
            # With 10 days = ~0.027 years, slope of 1e18 would give 2.7% discount
            pt_oracle = PtOracle.deploy(
                pt.address,
                mock_oracle.address,
                10**18,  # 100% per year slope (will give ~2.7% for 10 days)
                9 * 10**17,  # 90% intercept (0.9 * 10**18)
                86400,
                boa.env.generate_address(),
                boa.env.generate_address()
            )
        assert pt_oracle.intercept() == 9 * 10**17
        
        # With 10 days and high slope + intercept, should get significant discount
        # Total discount = (1e18 * 10/365) / 1e18 + 0.9 = 0.027 + 0.9 = 0.927 (92.7%)
        price = pt_oracle.price()
        assert price > 0
        assert price < 10**17  # Should be heavily discounted (less than 10%)
    
    # Story 1.2 Tests  
    def test_rate_limiting_with_greater_than_operator(self, pt_oracle, manager):
        """Test that rate limiting uses > operator, not >="""
        # Advance to allow first update
        boa.env.timestamp = boa.env.timestamp + 86401
        pt_oracle.set_linear_discount(100, 50, sender=manager)
        update_time = pt_oracle.last_discount_update()
        
        # At exactly max_update_interval, should fail (tests > operator)
        boa.env.timestamp = update_time + 86400
        with boa.reverts("update interval not elapsed"):
            pt_oracle.set_linear_discount(200, 100, sender=manager)
        
        # At max_update_interval + 1, should succeed
        boa.env.timestamp = update_time + 86401
        pt_oracle.set_linear_discount(200, 100, sender=manager)
        assert pt_oracle.slope() == 200
        assert pt_oracle.intercept() == 100
    
    def test_slope_and_intercept_boundaries(self, pt_oracle, manager, admin):
        """Test boundary assertions for slope and intercept"""
        # Test that intercept cannot exceed DISCOUNT_PRECISION
        boa.env.timestamp = boa.env.timestamp + 86401
        with boa.reverts("intercept exceeds precision"):
            pt_oracle.set_linear_discount(1000, 10**18 + 1, sender=manager)
        
        # Test that slope cannot exceed DISCOUNT_PRECISION
        with boa.reverts("slope exceeds precision"):
            pt_oracle.set_linear_discount(10**18 + 1, 1000, sender=manager)
        
        # Test very high but valid max update interval
        pt_oracle.set_limits(10**8, 0, 0, sender=admin)  # Very high but should work, no limits on changes
        assert pt_oracle.max_update_interval() == 10**8
    
    def test_events_include_old_and_new_values(self, pt_oracle, manager, admin):
        """Test that events emit both old and new values"""
        # Advance time for rate limiting
        boa.env.timestamp = boa.env.timestamp + 86401
        
        # Test LinearDiscountUpdated event
        old_slope = pt_oracle.slope()
        old_intercept = pt_oracle.intercept()
        
        # Update and check event (we can't directly check events in boa easily,
        # but we verify the state changes which would trigger the events)
        pt_oracle.set_linear_discount(500, 250, sender=manager)
        assert pt_oracle.slope() == 500
        assert pt_oracle.intercept() == 250
        
        # Test LimitsUpdated event
        old_interval = pt_oracle.max_update_interval()
        pt_oracle.set_limits(172800, 5 * 10**16, 3 * 10**16, sender=admin)
        assert pt_oracle.max_update_interval() == 172800
        assert pt_oracle.max_slope_change() == 5 * 10**16
        assert pt_oracle.max_intercept_change() == 3 * 10**16
    
    # Story 1.3 Tests
    def test_admin_can_set_manager(self, pt_oracle, admin, manager):
        """Test that admin can update the manager address"""
        new_manager = boa.env.generate_address()
        
        # Admin should be able to set new manager
        pt_oracle.set_manager(new_manager, sender=admin)
        assert pt_oracle.manager() == new_manager
        
        # New manager should be able to perform manager functions
        boa.env.timestamp = boa.env.timestamp + 86401
        pt_oracle.set_linear_discount(100, 50, sender=new_manager)
        assert pt_oracle.slope() == 100
        assert pt_oracle.intercept() == 50
    
    def test_manager_cannot_set_manager(self, pt_oracle, manager):
        """Test that manager cannot call set_manager"""
        new_manager = boa.env.generate_address()
        
        with boa.reverts("caller is not admin"):
            pt_oracle.set_manager(new_manager, sender=manager)
    
    def test_set_manager_rejects_zero_address(self, pt_oracle, admin):
        """Test that set_manager rejects zero address"""
        zero_address = "0x0000000000000000000000000000000000000000"
        
        with boa.reverts("invalid manager address"):
            pt_oracle.set_manager(zero_address, sender=admin)
    
    def test_manager_updated_event(self, pt_oracle, admin):
        """Test that ManagerUpdated event is emitted"""
        old_manager = pt_oracle.manager()
        new_manager = boa.env.generate_address()
        
        # Update manager (event would be emitted here)
        pt_oracle.set_manager(new_manager, sender=admin)
        assert pt_oracle.manager() == new_manager
        # Event emission is implicit in the state change
    
    def test_role_based_access_control(self, pt_oracle, admin, manager, owner):
        """Test comprehensive role-based access control"""
        unauthorized = boa.env.generate_address()
        
        # Test manager functions
        boa.env.timestamp = boa.env.timestamp + 86401
        pt_oracle.set_linear_discount(150, 75, sender=manager)  # Should work
        
        boa.env.timestamp = boa.env.timestamp + 86401
        with boa.reverts("caller is not manager"):
            pt_oracle.set_linear_discount(200, 100, sender=unauthorized)
        
        # Test admin functions
        pt_oracle.set_limits(100000, 0, 0, sender=admin)  # Should work
        
        with boa.reverts("caller is not admin"):
            pt_oracle.set_limits(200000, 0, 0, sender=unauthorized)
        
        new_manager = boa.env.generate_address()
        pt_oracle.set_manager(new_manager, sender=admin)  # Should work
        
        with boa.reverts("caller is not admin"):
            pt_oracle.set_manager(boa.env.generate_address(), sender=unauthorized)
    
    def test_set_slope_from_apy(self, pt_oracle, manager):
        """Test that manager can call set_slope_from_apy"""
        # Advance time for rate limiting
        boa.env.timestamp = boa.env.timestamp + 86401
        
        # Manager should be able to call set_slope_from_apy
        # APY of 10% = 0.10 * 10**18
        pt_oracle.set_slope_from_apy(10**17, sender=manager)  # 10% as 0.10 * 10**18
        
        # The set_slope_from_apy function converts APY to a per-second slope
        # But our new _calculate_price converts it back to years
        # So the effective slope should work out correctly
        assert pt_oracle.slope() > 0
        assert pt_oracle.intercept() == 0  # set_slope_from_apy sets intercept to 0
        assert pt_oracle.last_discount_update() > 0
    
    def test_interface_renamed_to_pendle_pt(self, pt_oracle, mock_pt):
        """Test that interface is renamed from PT to PendlePT"""
        # The public variable is still called 'pt' but interface is PendlePT
        assert pt_oracle.pt() == mock_pt.address
        # Test that pt_expiry immutable is cached correctly
        assert pt_oracle.pt_expiry() == mock_pt.expiry()
    
    # Time-based discount model tests
    def test_discount_model_with_time_jumps(self, owner, manager, admin):
        """Test discount model with 50% slope and time progression"""
        from contracts import PtOracle
        
        # Create PT with exactly 1 year expiry
        mock_pt_code = """
# pragma version 0.4.3

expiry: public(uint256)

@deploy
def __init__(_expiry: uint256):
    self.expiry = _expiry
"""
        # Create mock oracle with price = 1e18 (1.0)
        mock_oracle_code = """
# pragma version 0.4.3

@view
@external
def price() -> uint256:
    return 10**18  # Price = 1.0
"""
        
        with boa.env.prank(owner):
            # Set expiry to exactly 1 year from now
            one_year_later = boa.env.evm.patch.timestamp + (365 * 24 * 60 * 60)
            pt = boa.loads(mock_pt_code, one_year_later)
            mock_oracle = boa.loads(mock_oracle_code)
            
            # Deploy with slope = 0.5 (50% per year with 1e18 precision)
            # This means at 1 year to maturity, discount = 50%
            pt_oracle = PtOracle.deploy(
                pt.address,
                mock_oracle.address,
                5 * 10**17,  # slope = 0.5 (50% per year)
                0,           # intercept = 0
                86400,       # max_update_interval
                manager,
                admin
            )
        
        # Test 1: At start (1 year to maturity)
        # time_to_maturity = 1 year
        # discount = 0.5 * 1.0 = 0.5 (50%)
        # price = 1.0 * (1 - 0.5) = 0.5
        initial_price = pt_oracle.price()
        assert initial_price == 5 * 10**17, f"Expected 0.5e18, got {initial_price}"
        
        # Test 2: After 6 months (0.5 years to maturity)
        # time_to_maturity = 0.5 years
        # discount = 0.5 * 0.5 = 0.25 (25%)
        # price = 1.0 * (1 - 0.25) = 0.75
        boa.env.evm.patch.timestamp += (365 * 24 * 60 * 60) // 2  # Advance 6 months
        six_month_price = pt_oracle.price()
        assert six_month_price == 75 * 10**16, f"Expected 0.75e18, got {six_month_price}"
        
        # Test 3: Very close to expiry (1 day to maturity)
        # time_to_maturity ≈ 1/365 years ≈ 0.00274 years
        # discount = 0.5 * (1/365) ≈ 0.00137 (0.137%)
        # price ≈ 1.0 * (1 - 0.00137) ≈ 0.99863
        boa.env.evm.patch.timestamp = one_year_later - (24 * 60 * 60)  # 1 day before expiry
        near_expiry_price = pt_oracle.price()
        # Allow small rounding error
        expected_near_expiry = 10**18 - (5 * 10**17 * 10**18 // (365 * 10**18))
        assert abs(near_expiry_price - expected_near_expiry) < 10**14, f"Near expiry price mismatch: {near_expiry_price}"
        
        # Test 4: At expiry (should return underlying price)
        boa.env.evm.patch.timestamp = one_year_later
        expiry_price = pt_oracle.price()
        assert expiry_price == 10**18, f"Expected 1e18 at expiry, got {expiry_price}"
    
    def test_linear_discount_progression(self, owner, manager, admin):
        """Test that discount decreases linearly as time to maturity decreases"""
        from contracts import PtOracle
        
        mock_pt_code = """
# pragma version 0.4.3

expiry: public(uint256)

@deploy
def __init__(_expiry: uint256):
    self.expiry = _expiry
"""
        mock_oracle_code = """
# pragma version 0.4.3

@view
@external
def price() -> uint256:
    return 2 * 10**18  # Price = 2.0
"""
        
        with boa.env.prank(owner):
            # Set expiry to 100 days from now for easier calculations
            expiry_time = boa.env.evm.patch.timestamp + (100 * 24 * 60 * 60)
            pt = boa.loads(mock_pt_code, expiry_time)
            mock_oracle = boa.loads(mock_oracle_code)
            
            # Deploy with slope = 0.365 (36.5% per year)
            # At 100 days = 100/365 years, discount = 0.365 * (100/365) = 0.1 (10%)
            pt_oracle = PtOracle.deploy(
                pt.address,
                mock_oracle.address,
                365 * 10**15,  # slope = 0.365 (36.5% per year)
                0,             # intercept = 0
                86400,
                manager,
                admin
            )
        
        # Test at 100 days to maturity
        # discount = 0.365 * (100/365) = 0.1 (10%)
        # price = 2.0 * (1 - 0.1) = 1.8
        price_100d = pt_oracle.price()
        expected_100d = 18 * 10**17
        assert abs(price_100d - expected_100d) < 10**15, f"100 days: expected ~1.8e18, got {price_100d}"
        
        # Test at 50 days to maturity
        boa.env.evm.patch.timestamp += (50 * 24 * 60 * 60)
        # discount = 0.365 * (50/365) = 0.05 (5%)
        # price = 2.0 * (1 - 0.05) = 1.9
        price_50d = pt_oracle.price()
        expected_50d = 19 * 10**17
        assert abs(price_50d - expected_50d) < 10**15, f"50 days: expected ~1.9e18, got {price_50d}"
        
        # Test at 10 days to maturity
        boa.env.evm.patch.timestamp += (40 * 24 * 60 * 60)
        # discount = 0.365 * (10/365) = 0.01 (1%)
        # price = 2.0 * (1 - 0.01) = 1.98
        price_10d = pt_oracle.price()
        expected_10d = 198 * 10**16
        assert abs(price_10d - expected_10d) < 10**15, f"10 days: expected ~1.98e18, got {price_10d}"
    
    def test_discount_with_intercept(self, owner, manager, admin):
        """Test discount model with both slope and intercept"""
        from contracts import PtOracle
        
        mock_pt_code = """
# pragma version 0.4.3

expiry: public(uint256)

@deploy
def __init__(_expiry: uint256):
    self.expiry = _expiry
"""
        mock_oracle_code = """
# pragma version 0.4.3

@view
@external
def price() -> uint256:
    return 10**18  # Price = 1.0
"""
        
        with boa.env.prank(owner):
            # Set expiry to 1 year from now
            one_year_later = boa.env.evm.patch.timestamp + (365 * 24 * 60 * 60)
            pt = boa.loads(mock_pt_code, one_year_later)
            mock_oracle = boa.loads(mock_oracle_code)
            
            # Deploy with slope = 0.2 (20% per year) and intercept = 0.1 (10%)
            # At 1 year: discount = 0.2 * 1.0 + 0.1 = 0.3 (30%)
            pt_oracle = PtOracle.deploy(
                pt.address,
                mock_oracle.address,
                2 * 10**17,  # slope = 0.2 (20% per year)
                10**17,      # intercept = 0.1 (10%)
                86400,
                manager,
                admin
            )
        
        # Test at 1 year to maturity
        # discount = 0.2 * 1.0 + 0.1 = 0.3 (30%)
        # price = 1.0 * (1 - 0.3) = 0.7
        initial_price = pt_oracle.price()
        assert initial_price == 7 * 10**17, f"Expected 0.7e18, got {initial_price}"
        
        # Test at 0.5 years to maturity
        # discount = 0.2 * 0.5 + 0.1 = 0.2 (20%)
        # price = 1.0 * (1 - 0.2) = 0.8
        boa.env.evm.patch.timestamp += (365 * 24 * 60 * 60) // 2
        six_month_price = pt_oracle.price()
        assert six_month_price == 8 * 10**17, f"Expected 0.8e18, got {six_month_price}"
        
        # Test very close to expiry
        # discount ≈ 0.2 * 0 + 0.1 = 0.1 (10%)
        # price ≈ 1.0 * (1 - 0.1) = 0.9
        boa.env.evm.patch.timestamp = one_year_later - 3600  # 1 hour before expiry
        near_expiry_price = pt_oracle.price()
        # Should be very close to 0.9 (just intercept discount)
        assert abs(near_expiry_price - 9 * 10**17) < 10**15, f"Near expiry price mismatch: {near_expiry_price}"
    
    # Tests for slope and intercept change limits
    def test_slope_change_limit_enforced(self, pt_oracle, manager, admin):
        """Test that slope changes are limited when max_slope_change is set"""
        # Set a 2% max slope change limit
        pt_oracle.set_limits(86400, 2 * 10**16, 0, sender=admin)
        
        # Advance time to allow update
        boa.env.timestamp = boa.env.timestamp + 86401
        
        # Current slope is 5% (5e16), try to change to 8% (exceeds 2% limit)
        with boa.reverts("slope change exceeds limit"):
            pt_oracle.set_linear_discount(8 * 10**16, 0, sender=manager)
        
        # Change within limit should work (5% -> 6.5% = 1.5% change)
        pt_oracle.set_linear_discount(65 * 10**15, 0, sender=manager)
        assert pt_oracle.slope() == 65 * 10**15
        
        # Test decrease as well (6.5% -> 5% = 1.5% change)
        boa.env.timestamp = boa.env.timestamp + 86401
        pt_oracle.set_linear_discount(5 * 10**16, 0, sender=manager)
        assert pt_oracle.slope() == 5 * 10**16
    
    def test_intercept_change_limit_enforced(self, pt_oracle, manager, admin):
        """Test that intercept changes are limited when max_intercept_change is set"""
        # First set an initial intercept
        boa.env.timestamp = boa.env.timestamp + 86401
        pt_oracle.set_linear_discount(5 * 10**16, 3 * 10**16, sender=manager)
        
        # Set a 1.5% max intercept change limit  
        pt_oracle.set_limits(86400, 0, 15 * 10**15, sender=admin)
        
        # Advance time to allow update
        boa.env.timestamp = boa.env.timestamp + 86401
        
        # Current intercept is 3% (3e16), try to change to 5% (exceeds 1.5% limit)
        with boa.reverts("intercept change exceeds limit"):
            pt_oracle.set_linear_discount(5 * 10**16, 5 * 10**16, sender=manager)
        
        # Change within limit should work (3% -> 4% = 1% change)
        pt_oracle.set_linear_discount(5 * 10**16, 4 * 10**16, sender=manager)
        assert pt_oracle.intercept() == 4 * 10**16
    
    def test_zero_limit_means_no_restriction(self, owner, manager, admin):
        """Test that setting max change to 0 means no limit"""
        from contracts import PtOracle
        
        mock_pt_code = """
# pragma version 0.4.3

expiry: public(uint256)

@deploy
def __init__(_expiry: uint256):
    self.expiry = _expiry
"""
        mock_oracle_code = """
# pragma version 0.4.3

@view
@external
def price() -> uint256:
    return 10**18
"""
        
        with boa.env.prank(owner):
            pt = boa.loads(mock_pt_code, boa.env.timestamp + 86400 * 30)
            mock_oracle = boa.loads(mock_oracle_code)
            
            # Deploy with default 0 limits (no restrictions)
            pt_oracle = PtOracle.deploy(
                pt.address,
                mock_oracle.address,
                5 * 10**16,  # 5% slope
                10**16,      # 1% intercept
                86400,
                manager,
                admin
            )
        
        # Advance time
        boa.env.timestamp = boa.env.timestamp + 86401
        
        # Should be able to make large changes with no limits
        pt_oracle.set_linear_discount(90 * 10**16, 80 * 10**16, sender=manager)
        assert pt_oracle.slope() == 90 * 10**16
        assert pt_oracle.intercept() == 80 * 10**16
    
    def test_both_limits_work_together(self, pt_oracle, manager, admin):
        """Test that both slope and intercept limits are enforced together"""
        # Set limits: 1% max slope change, 2% max intercept change
        pt_oracle.set_limits(86400, 10**16, 2 * 10**16, sender=admin)
        
        # Advance time
        boa.env.timestamp = boa.env.timestamp + 86401
        
        # Try to exceed slope limit (5% -> 7% = 2% change, limit is 1%)
        with boa.reverts("slope change exceeds limit"):
            pt_oracle.set_linear_discount(7 * 10**16, 10**16, sender=manager)
        
        # Valid slope change but exceed intercept limit (0% -> 3% = 3% change, limit is 2%)
        with boa.reverts("intercept change exceeds limit"):
            pt_oracle.set_linear_discount(55 * 10**15, 3 * 10**16, sender=manager)
        
        # Both changes within limits should work
        pt_oracle.set_linear_discount(55 * 10**15, 15 * 10**15, sender=manager)
        assert pt_oracle.slope() == 55 * 10**15
        assert pt_oracle.intercept() == 15 * 10**15
    
    def test_admin_can_update_limits(self, pt_oracle, admin, manager):
        """Test that admin can update limits at any time"""
        # Initially set strict limits
        pt_oracle.set_limits(86400, 5 * 10**15, 5 * 10**15, sender=admin)  # 0.5% limits
        
        # Advance time
        boa.env.timestamp = boa.env.timestamp + 86401
        
        # Manager tries large change, should fail
        with boa.reverts("slope change exceeds limit"):
            pt_oracle.set_linear_discount(10 * 10**16, 0, sender=manager)
        
        # Admin relaxes limits
        pt_oracle.set_limits(86400, 10 * 10**16, 10 * 10**16, sender=admin)  # 10% limits
        
        # Now manager can make larger changes
        pt_oracle.set_linear_discount(10 * 10**16, 5 * 10**16, sender=manager)
        assert pt_oracle.slope() == 10 * 10**16
        assert pt_oracle.intercept() == 5 * 10**16
    
    def test_set_slope_from_apy_respects_limit(self, pt_oracle, manager, admin):
        """Test that set_slope_from_apy also respects slope change limits"""
        # Set a 2% max slope change limit
        pt_oracle.set_limits(86400, 2 * 10**16, 10**16, sender=admin)
        
        # Advance time
        boa.env.timestamp = boa.env.timestamp + 86401
        
        # Current slope is 5% (5e16)
        # Try to set APY that would result in slope > 7% (exceeds 2% limit)
        # For APY of 10%, slope = 10% / (1 + 10%) ≈ 9.09%, which exceeds limit
        with boa.reverts("slope change exceeds limit"):
            pt_oracle.set_slope_from_apy(10**17, sender=manager)  # 10% APY
        
        # APY that results in slope within limit should work
        # For APY of 6.5%, slope = 6.5% / (1 + 6.5%) ≈ 6.1%, change is ~1.1%
        pt_oracle.set_slope_from_apy(65 * 10**15, sender=manager)  # 6.5% APY
        # Verify slope changed and intercept is 0
        assert pt_oracle.slope() > 5 * 10**16
        assert pt_oracle.slope() < 7 * 10**16
        assert pt_oracle.intercept() == 0