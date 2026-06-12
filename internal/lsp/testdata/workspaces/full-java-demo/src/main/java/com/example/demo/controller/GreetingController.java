package com.example.demo.controller;

import com.example.demo.api.Greeter;
import com.example.demo.service.DefaultGreeter;

public class GreetingController {

    private Greeter greeter = new DefaultGreeter();

    public String greeting(String name) {
        return greeter.greet(name);
    }
}

