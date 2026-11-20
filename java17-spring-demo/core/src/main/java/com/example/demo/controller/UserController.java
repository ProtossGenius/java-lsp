package com.example.demo.controller;

import java.util.List;

import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

import com.example.demo.model.CreateUserRequest;
import com.example.demo.model.User;
import com.example.demo.service.UserService;

import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;

@Slf4j
@RestController
@RequestMapping("/api/users")
@RequiredArgsConstructor
public class UserController {

  private final UserService userService;

  @GetMapping
  public List<User> listUsers() {
    log.error("Sample stack trace for jump testing", new RuntimeException("debug-stack-sample"));
    return userService.listUsers();
  }

  @GetMapping("/{id}")
  public User getUser(@PathVariable Long id) {
    log.info("Handling GET /api/users/{}", id);
    log.info("Handling GET /api/users/{}", id);
    log.info("Handling GET /api/users/{}", id);
    log.info("Handling GET /api/users/{}", id);
    log.info("Handling GET /api/users/{}", id);
    for (int i = 0; i < 100; ++i) {
      log.info("Handling GET /api/users/{}", id);
       String.format("hello world %d", id);
    }
    return userService.getUser(id);
  }

  @PostMapping
  @ResponseStatus(HttpStatus.CREATED)
  public User createUser(@Valid @RequestBody CreateUserRequest request) {
    log.info("Handling POST /api/users for {}", request.getEmail());
    return userService.createUser(request);
  }
}
