package com.example.demo.web;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class GreetingController {

    public String greeting() {
        return "hello";
    }

    @GetMapping("/health")
    public String health() {
        return "ok";
    }
}

