package com.example.demo;

import org.mybatis.spring.annotation.MapperScan;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.elasticsearch.ElasticsearchClientAutoConfiguration;
import org.springframework.boot.autoconfigure.elasticsearch.ElasticsearchRestClientAutoConfiguration;
import org.springframework.boot.autoconfigure.SpringBootApplication;

import lombok.extern.slf4j.Slf4j;

@Slf4j
@MapperScan("com.example.demo.mapper")
@SpringBootApplication(exclude = {
  ElasticsearchClientAutoConfiguration.class,
  ElasticsearchRestClientAutoConfiguration.class,
})
public class DemoApplication {

  public static void main(String[] args) {
    log.info("Starting java17-spring-demo on Java {}", System.getProperty("java.version"));
    SpringApplication.run(DemoApplication.class, args);
  }
}
