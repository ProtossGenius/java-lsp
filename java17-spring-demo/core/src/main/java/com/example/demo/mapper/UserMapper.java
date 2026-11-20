package com.example.demo.mapper;

import java.util.List;
import java.util.Optional;

import org.apache.ibatis.annotations.Param;

import com.example.demo.model.User;
import com.example.demo.model.UserEnum;

public interface UserMapper {

  List<User> findAll();

  Optional<User> findById(@Param("id") Long id);

  int insert(@Param("user") User user) ;

  void updateStatus(Integer id, UserEnum eu);

  default List<User> chaosFindAll() {
    return findAll();
  }
}
