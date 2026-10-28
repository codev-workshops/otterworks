package com.otterworks.auth.service;

import static net.logstash.logback.argument.StructuredArguments.kv;

import com.otterworks.auth.dto.AuthResponse;
import com.otterworks.auth.dto.ChangePasswordRequest;
import com.otterworks.auth.dto.LoginRequest;
import com.otterworks.auth.dto.RegisterRequest;
import com.otterworks.auth.entity.RefreshToken;
import com.otterworks.auth.entity.User;
import com.otterworks.auth.observability.AuthMetrics;
import com.otterworks.auth.repository.RefreshTokenRepository;
import com.otterworks.auth.repository.UserRepository;
import com.otterworks.auth.security.JwtTokenProvider;
import io.jsonwebtoken.JwtException;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.Set;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class AuthService {

  private static final Logger log = LoggerFactory.getLogger(AuthService.class);

  private final UserRepository userRepository;
  private final PasswordEncoder passwordEncoder;
  private final JwtTokenProvider jwtTokenProvider;
  private final RefreshTokenRepository refreshTokenRepository;
  private final AuthMetrics metrics;

  public AuthService(
      UserRepository userRepository,
      PasswordEncoder passwordEncoder,
      JwtTokenProvider jwtTokenProvider,
      RefreshTokenRepository refreshTokenRepository,
      AuthMetrics metrics) {
    this.metrics = metrics;
    this.userRepository = userRepository;
    this.passwordEncoder = passwordEncoder;
    this.jwtTokenProvider = jwtTokenProvider;
    this.refreshTokenRepository = refreshTokenRepository;
  }

  @Transactional
  public AuthResponse register(RegisterRequest request) {
    if (userRepository.existsByEmail(request.getEmail())) {
      metrics.registration("duplicate_email");
      log.info("Registration rejected {}", kv("reason", "duplicate_email"));
      throw new IllegalArgumentException("Email already registered");
    }

    User user = new User();
    user.setEmail(request.getEmail());
    user.setPasswordHash(passwordEncoder.encode(request.getPassword()));
    user.setDisplayName(request.getDisplayName());
    user.setRoles(Set.of(User.Role.USER));
    user = userRepository.save(user);

    metrics.registration(AuthMetrics.SUCCESS);
    log.info("User registered {}", kv("user_id", user.getId()));
    return buildAuthResponse(user);
  }

  @Transactional
  public AuthResponse login(LoginRequest request) {
    User user = userRepository.findByEmail(request.getEmail()).orElse(null);
    if (user == null) {
      metrics.login("unknown_user");
      log.warn("Login failed {}", kv("reason", "unknown_user"));
      throw new IllegalArgumentException("Invalid credentials");
    }

    if (!passwordEncoder.matches(request.getPassword(), user.getPasswordHash())) {
      metrics.login("bad_password");
      log.warn("Login failed {} {}", kv("reason", "bad_password"), kv("user_id", user.getId()));
      throw new IllegalArgumentException("Invalid credentials");
    }

    user.setLastLoginAt(Instant.now());
    userRepository.save(user);

    metrics.login(AuthMetrics.SUCCESS);
    log.info("User logged in {}", kv("user_id", user.getId()));
    return buildAuthResponse(user);
  }

  @Transactional
  public AuthResponse refreshToken(String token) {
    String jti;
    String userId;
    try {
      jti = jwtTokenProvider.extractJti(token);
      userId = jwtTokenProvider.validateRefreshTokenAndGetUserId(token);
    } catch (JwtException | IllegalArgumentException e) {
      rejectRefresh("invalid_token", null);
      throw e;
    }

    RefreshToken storedToken =
        refreshTokenRepository.findByTokenIdAndRevokedFalse(jti).orElse(null);
    if (storedToken == null) {
      rejectRefresh("revoked", userId);
      throw new IllegalArgumentException("Invalid or revoked refresh token");
    }

    if (storedToken.getExpiresAt().isBefore(Instant.now())) {
      rejectRefresh("expired", userId);
      throw new IllegalArgumentException("Refresh token expired");
    }

    storedToken.setRevoked(true);
    refreshTokenRepository.save(storedToken);

    User user =
        userRepository
            .findById(UUID.fromString(userId))
            .orElseThrow(() -> new IllegalArgumentException("User not found"));

    metrics.tokenRefresh(AuthMetrics.SUCCESS);
    log.info("Token refreshed {}", kv("user_id", user.getId()));
    return buildAuthResponse(user);
  }

  @Transactional
  public void changePassword(UUID userId, ChangePasswordRequest request) {
    User user =
        userRepository
            .findById(userId)
            .orElseThrow(() -> new IllegalArgumentException("User not found"));

    if (!passwordEncoder.matches(request.getCurrentPassword(), user.getPasswordHash())) {
      metrics.passwordChange("bad_password");
      log.warn(
          "Password change rejected {} {}", kv("reason", "bad_password"), kv("user_id", userId));
      throw new IllegalArgumentException("Current password is incorrect");
    }

    user.setPasswordHash(passwordEncoder.encode(request.getNewPassword()));
    userRepository.save(user);

    refreshTokenRepository.revokeAllByUserId(userId);
    metrics.passwordChange(AuthMetrics.SUCCESS);
    log.info("Password changed {}", kv("user_id", userId));
  }

  @Transactional
  public void logout(UUID userId) {
    refreshTokenRepository.revokeAllByUserId(userId);
    metrics.logout();
    log.info("User logged out {}", kv("user_id", userId));
  }

  private void rejectRefresh(String reason, String userId) {
    metrics.tokenRefresh(reason);
    log.warn("Token refresh rejected {} {}", kv("reason", reason), kv("user_id", userId));
  }

  private AuthResponse buildAuthResponse(User user) {
    String accessToken = jwtTokenProvider.generateAccessToken(user);
    String refreshTokenStr = jwtTokenProvider.generateRefreshToken(user);

    String jti = jwtTokenProvider.extractJti(refreshTokenStr);
    RefreshToken refreshToken = new RefreshToken();
    refreshToken.setUser(user);
    refreshToken.setTokenId(jti);
    refreshToken.setExpiresAt(
        Instant.now().plus(jwtTokenProvider.getRefreshTokenExpiry(), ChronoUnit.SECONDS));
    refreshTokenRepository.save(refreshToken);

    return new AuthResponse(
        accessToken,
        refreshTokenStr,
        "Bearer",
        jwtTokenProvider.getAccessTokenExpiry(),
        new AuthResponse.UserDto(
            user.getId().toString(), user.getEmail(), user.getDisplayName(), user.getAvatarUrl()));
  }
}
