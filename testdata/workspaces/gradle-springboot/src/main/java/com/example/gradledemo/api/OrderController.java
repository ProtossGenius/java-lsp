package com.example.gradledemo.api;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class OrderController {

    @GetMapping("/orders/summary")
    public String summary() {
        return "summary";
    }
}

