-- CampusQuery AI - database/setup.sql
-- Matches the schema already in use: departments -> courses -> students,
-- with `id` primary keys (not student_id/course_id/department_id) and
-- no `email` column on students.

CREATE DATABASE IF NOT EXISTS college_db;
USE college_db;

CREATE TABLE IF NOT EXISTS departments (
    id INT AUTO_INCREMENT PRIMARY KEY,
    department_name VARCHAR(100) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS courses (
    id INT AUTO_INCREMENT PRIMARY KEY,
    course_name VARCHAR(100) NOT NULL,
    duration INT NOT NULL,
    department_id INT,
    FOREIGN KEY (department_id)
        REFERENCES departments(id)
        ON DELETE SET NULL
        ON UPDATE CASCADE
);

CREATE TABLE IF NOT EXISTS students (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    course_id INT,
    marks INT,
    city VARCHAR(100),
    FOREIGN KEY (course_id)
        REFERENCES courses(id)
        ON DELETE SET NULL
        ON UPDATE CASCADE
);

INSERT INTO departments (department_name) VALUES
('Computer Science'),
('Information Technology'),
('Commerce');

INSERT INTO courses (course_name, duration, department_id) VALUES
('B.Sc. Computer Science', 3, 1),
('B.Sc. Information Technology', 3, 2),
('MCA', 2, 2),
('B.Com', 3, 3);

INSERT INTO students (name, course_id, marks, city) VALUES
('Rahul Sharma', 1, 95, 'Mumbai'),
('Priya Patil', 2, 88, 'Pune'),
('Amit Shah', 3, 76, 'Mumbai'),
('Sneha Joshi', 2, 92, 'Nashik'),
('Rohan Mehta', 1, 81, 'Thane'),
('Neha Desai', 4, 85, 'Mumbai'),
('Arjun Kulkarni', 3, 90, 'Pune'),
('Pooja Singh', 2, 72, 'Navi Mumbai');
