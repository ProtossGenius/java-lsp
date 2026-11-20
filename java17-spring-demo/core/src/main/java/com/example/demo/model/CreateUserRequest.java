package com.example.demo.model;

import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;
import lombok.Data;

@Data
public class CreateUserRequest {

  @NotBlank
  private String name;

  @Email
  @NotBlank
  private String email;
}
