package com.example.common;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.ComponentScan;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableScheduling;

/** Registers all shared beans in every service that has the common module on its classpath. */
@Configuration
@ComponentScan("com.example.common")
@EnableScheduling
@EnableConfigurationProperties(PlatformProperties.class)
public class CommonAutoConfiguration {
}
