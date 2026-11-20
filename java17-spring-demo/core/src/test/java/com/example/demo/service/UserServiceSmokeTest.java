package com.example.demo.service;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

import com.example.demo.model.User;

@SpringBootTest
class UserServiceSmokeTest {

  @Autowired
  private UserService userService;

  @Test
  void shouldLoadSeedUsers() {
    List<User> users = userService.listUsers();
    assertThat(users)
      .extracting(User::getEmail)
      .contains("alice@example.com", "bob@example.com");
  }
}
