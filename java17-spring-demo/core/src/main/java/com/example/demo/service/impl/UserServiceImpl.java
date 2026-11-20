package com.example.demo.service.impl;

import java.util.List;

import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import com.example.demo.mapper.UserMapper;
import com.example.demo.model.CreateUserRequest;
import com.example.demo.model.User;
import com.example.demo.service.UserService;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;

@Slf4j
@Service
@RequiredArgsConstructor
public class UserServiceImpl implements UserService {

  private final UserMapper userMapper;

  @Override
  public List<User> listUsers() {
    log.info("Listing all users");
    return userMapper.findAll();
  }

  @Override
  public User getUser(Long id) {
    log.info("Loading user by id={}", id);
    return userMapper.findById(id)
      .orElseThrow(() -> new IllegalArgumentException("User not found: " + id));
  }

  @Override
  @Transactional
  public User createUser(CreateUserRequest request) {
    User user = User.builder()
      .name(request.getName())
      .email(request.getEmail())
      .build();

    log.info("Creating user with email={}", request.getEmail());
    userMapper.insert(user);
    return user;
  }
}
