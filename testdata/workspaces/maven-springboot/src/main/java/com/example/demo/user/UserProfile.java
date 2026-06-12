package com.example.demo.user;

import lombok.Getter;

public class UserProfile {

    @Getter
    private String name;

    private int loginCount;

    public int loginCount() {
        return loginCount;
    }
}

