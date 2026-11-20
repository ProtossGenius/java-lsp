package com.example.demo.service;

import java.util.List;

import com.example.demo.model.CreateUserRequest;
import com.example.demo.model.User;

public interface UserService {

  List<User> listUsers();

  User getUser(Long id);

  User createUser(CreateUserRequest request);
}
