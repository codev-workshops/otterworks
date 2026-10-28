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
import java.time.Instant;
import java.time.ZoneOffset;
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
    this.userRepository = userRepository;
    this.passwordEncoder = passwordEncoder;
    this.jwtTokenProvider = jwtTokenProvider;
    this.refreshTokenRepository = refreshTokenRepository;
    this.metrics = metrics;
  }

  @Transactional
  public AuthResponse register(RegisterRequest request) {
    return metrics.time("register", () -> doRegister(request));
  }

  private AuthResponse doRegister(RegisterRequest request) {
    if (userRepository.existsByEmail(request.getEmail())) {
      metrics.registration("duplicate_email");
      log.warn(
          "Registration rejected {} {} {}",
          kv("event", "auth.register"),
          kv("outcome", "duplicate_email"),
          kv("emailHash", AuthMetrics.hash(request.getEmail())));
      throw new IllegalArgumentException("Email already registered");
    }

    User user = new User();
    user.setEmail(request.getEmail());
    user.setPasswordHash(passwordEncoder.encode(request.getPassword()));
    user.setDisplayName(request.getDisplayName());
    user.setRoles(Set.of(User.Role.USER));
    user = userRepository.save(user);

    metrics.registration(AuthMetrics.SUCCESS);
    log.info(
        "User registered {} {} {}",
        kv("event", "auth.register"),
        kv("outcome", AuthMetrics.SUCCESS),
        kv("userIdHash", userIdHash(user)));
    return buildAuthResponse(user, "register");
  }

  @Transactional
  public AuthResponse login(LoginRequest request) {
    return metrics.time("login", () -> doLogin(request));
  }

  private AuthResponse doLogin(LoginRequest request) {
    User user = userRepository.findByEmail(request.getEmail()).orElse(null);
    if (user == null) {
      throw rejectLogin("unknown_user", kv("emailHash", AuthMetrics.hash(request.getEmail())));
    }

    if (!passwordEncoder.matches(request.getPassword(), user.getPasswordHash())) {
      throw rejectLogin("invalid_password", kv("userIdHash", userIdHash(user)));
    }

    Instant now = Instant.now();
    boolean firstLoginToday =
        user.getLastLoginAt() == null
            || user.getLastLoginAt()
                .isBefore(now.atOffset(ZoneOffset.UTC).truncatedTo(ChronoUnit.DAYS).toInstant());
    user.setLastLoginAt(now);
    userRepository.save(user);

    metrics.loginAttempt(AuthMetrics.SUCCESS, "none");
    if (firstLoginToday) {
      metrics.uniqueLogin();
    }
    log.info(
        "User logged in {} {} {}",
        kv("event", "auth.login"),
        kv("outcome", AuthMetrics.SUCCESS),
        kv("userIdHash", userIdHash(user)));
    return buildAuthResponse(user, "login");
  }

  private IllegalArgumentException rejectLogin(String reason, Object subject) {
    metrics.loginAttempt(AuthMetrics.FAILURE, reason);
    log.warn(
        "Login failed {} {} {} {}",
        kv("event", "auth.login"),
        kv("outcome", AuthMetrics.FAILURE),
        kv("reason", reason),
        subject);
    return new IllegalArgumentException("Invalid credentials");
  }

  @Transactional
  public AuthResponse refreshToken(String token) {
    return metrics.time("refresh", () -> doRefreshToken(token));
  }

  private AuthResponse doRefreshToken(String token) {
    String jti;
    String userId;
    try {
      jti = jwtTokenProvider.extractJti(token);
      userId = jwtTokenProvider.validateRefreshTokenAndGetUserId(token);
    } catch (RuntimeException e) {
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

    User user = userRepository.findById(UUID.fromString(userId)).orElse(null);
    if (user == null) {
      rejectRefresh("user_not_found", userId);
      throw new IllegalArgumentException("User not found");
    }

    metrics.tokenRefresh(AuthMetrics.SUCCESS);
    log.info(
        "Token refreshed {} {} {}",
        kv("event", "auth.token.refresh"),
        kv("outcome", AuthMetrics.SUCCESS),
        kv("userIdHash", userIdHash(user)));
    return buildAuthResponse(user, "refresh");
  }

  private void rejectRefresh(String reason, String userId) {
    metrics.tokenRefresh(reason);
    log.warn(
        "Token refresh rejected {} {} {}",
        kv("event", "auth.token.refresh"),
        kv("outcome", reason),
        kv("userIdHash", AuthMetrics.hash(userId)));
  }

  @Transactional
  public void changePassword(UUID userId, ChangePasswordRequest request) {
    metrics.time("change_password", () -> doChangePassword(userId, request));
  }

  private void doChangePassword(UUID userId, ChangePasswordRequest request) {
    User user = userRepository.findById(userId).orElse(null);
    if (user == null) {
      rejectPasswordChange("user_not_found", userId);
      throw new IllegalArgumentException("User not found");
    }

    if (!passwordEncoder.matches(request.getCurrentPassword(), user.getPasswordHash())) {
      rejectPasswordChange("invalid_password", userId);
      throw new IllegalArgumentException("Current password is incorrect");
    }

    user.setPasswordHash(passwordEncoder.encode(request.getNewPassword()));
    userRepository.save(user);

    refreshTokenRepository.revokeAllByUserId(userId);
    metrics.passwordChange(AuthMetrics.SUCCESS);
    log.info(
        "Password changed {} {} {}",
        kv("event", "auth.password.change"),
        kv("outcome", AuthMetrics.SUCCESS),
        kv("userIdHash", AuthMetrics.hash(userId.toString())));
  }

  private void rejectPasswordChange(String reason, UUID userId) {
    metrics.passwordChange(reason);
    log.warn(
        "Password change rejected {} {} {}",
        kv("event", "auth.password.change"),
        kv("outcome", reason),
        kv("userIdHash", AuthMetrics.hash(userId.toString())));
  }

  @Transactional
  public void logout(UUID userId) {
    metrics.time(
        "logout",
        () -> {
          refreshTokenRepository.revokeAllByUserId(userId);
          metrics.logout();
          log.info(
              "User logged out {} {} {}",
              kv("event", "auth.logout"),
              kv("outcome", AuthMetrics.SUCCESS),
              kv("userIdHash", AuthMetrics.hash(userId.toString())));
        });
  }

  private static String userIdHash(User user) {
    return user.getId() == null ? null : AuthMetrics.hash(user.getId().toString());
  }

  private AuthResponse buildAuthResponse(User user, String grant) {
    String accessToken = jwtTokenProvider.generateAccessToken(user);
    String refreshTokenStr = jwtTokenProvider.generateRefreshToken(user);

    String jti = jwtTokenProvider.extractJti(refreshTokenStr);
    RefreshToken refreshToken = new RefreshToken();
    refreshToken.setUser(user);
    refreshToken.setTokenId(jti);
    refreshToken.setExpiresAt(
        Instant.now().plus(jwtTokenProvider.getRefreshTokenExpiry(), ChronoUnit.SECONDS));
    refreshTokenRepository.save(refreshToken);
    metrics.tokenIssued("access", grant);
    metrics.tokenIssued("refresh", grant);

    return new AuthResponse(
        accessToken,
        refreshTokenStr,
        "Bearer",
        jwtTokenProvider.getAccessTokenExpiry(),
        new AuthResponse.UserDto(
            user.getId().toString(), user.getEmail(), user.getDisplayName(), user.getAvatarUrl()));
  }
}
